import discord
from discord.ext import commands, tasks

import wavelink

import re
import typing
import humanize

import asyncio
import datetime

import math
import random
import itertools

async def add_react(message, type_: bool):
    emoji = '<:checkmark:601123463859535885>' if type_ == True else '<:wrongmark:601124568387551232>'
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException:
        return

RURL = re.compile(r"https?:\/\/(?:www\.)?.+")


class Track(wavelink.Track):
    __slots__ = ('requester', 'channel', 'message', 'looped')

    def __init__(self, id_, info, *, ctx=None):
        super(Track, self).__init__(id_, info)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.message = ctx.message

    @property
    def is_dead(self):
        return self.dead

class Player(wavelink.Player):

    def __init__(self, bot: typing.Union[commands.Bot, commands.AutoShardedBot], guild_id: int, node: wavelink.Node):
        super(Player, self).__init__(bot, guild_id, node)

        self.queue = asyncio.Queue()
        self.next_event = asyncio.Event()

        self.volume = 80
        self.dj = None
        self.eq = 'Flat'
        
        self.repeat = None
        self.text_channel = None

        self.pauses = set()
        self.resumes = set()
        self.stops = set()
        self.shuffles = set()
        self.skips = set()
        self.repeats = set()

        bot.loop.create_task(self.player_loop())

    @property
    def entries(self):
        return list(self.queue._queue)

    async def player_loop(self):
        await self.bot.wait_until_ready()

        await self.set_preq('Flat')

        await self.set_volume(self.volume)

        while True:
            self.next_event.clear()

            self.inactive = False

            self.paused = False
            
            if self.repeat:
                track = self.repeat
            
            elif self.current and len(self.entries) == 0:
                track = None
                await self.text_channel.send(_(await get_language(self.bot, self.guild_id), "Kolejka skończyła się."))

            else:
                track = await self.queue.get()

            self.current = track

            if track is not None:
                await self.play(track)

                if not self.repeat:
                    await self.text_channel.send(_(await get_language(self.bot, self.guild_id), "Gram teraz `{}`.").format(self.current.title))
                    self.pauses.clear()
                    self.resumes.clear()
                    self.stops.clear()
                    self.shuffles.clear()
                    self.skips.clear()
                    self.repeats.clear()

                await self.next_event.wait()

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.initiate_nodes())
        self.leave_channels.start()

    async def initiate_nodes(self):
        nodes = {'MAIN': {'host': '0.0.0.0',
                          'port': 1334,
                          'rest_url': 'http://0.0.0.0:1334',
                          'password': "youshallnotpass",
                          'identifier': 'style',
                          'region': 'eu_central'}}

        for n in nodes.values():
            node = await self.bot.wavelink.initiate_node(host=n['host'],
                                                     port=n['port'],
                                                     rest_uri=n['rest_url'],
                                                     password=n['password'],
                                                     identifier=n['identifier'],
                                                     region=n['region'],
                                                     secure=False)

            node.set_hook(self.event_hook)

    @tasks.loop(minutes=10)
    async def leave_channels(self):
        for player in self.bot.wavelink.players:

            vc = player._voice_state

            if len(vc.channel.members) == 1:
                await vc.channel.disconnect()

    def event_hook(self, event):
        if isinstance(event, wavelink.TrackEnd):
            event.player.next_event.set()
        elif isinstance(event, wavelink.TrackException):
            print(event.error)

    def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        else:
            return True

    def required(self, player, invoked_with):
        channel = self.bot.get_channel(int(player.channel_id))
        if invoked_with == 'stop':
            if len(channel.members) - 1 == 2:
                return 2

        return math.ceil((len(channel.members) - 1) / 2.5)

    async def has_perms(self, ctx, **perms):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.author.id == player.dj.id:
            return True

        ch = ctx.channel
        permissions = ch.permissions_for(ctx.author)

        missing = [perm for perm, value in perms.items(
                    ) if getattr(permissions, perm, None) != value]

        if not missing:
            return True

        return False

    async def vote_check(self, ctx, command: str):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        vcc = len(self.bot.get_channel(int(player.channel_id)).members) - 1
        votes = getattr(player, command + 's', None)

        if vcc < 3 and not ctx.invoked_with == 'stop':
            votes.clear()
            return True
        else:
            votes.add(ctx.author.id)

            if len(votes) >= self.required(player, ctx.invoked_with):
                votes.clear()
                return True
        return False

    async def do_vote(self, ctx, player, command: str):
        attr = getattr(player, command + 's', None)
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.author.id in attr:
            await ctx.send(_(ctx.lang, "{}, już głosowałeś.").format(ctx.author.mention))
        elif await self.vote_check(ctx, command):
            await ctx.send(_(ctx.lang, "Przegłosowano `{}`.").format(command))
            to_do = getattr(self, f'do_{command}')
            await to_do(ctx)
            await add_react(ctx.message, True)
        else:
            await ctx.send(_(ctx.lang, "{}, zagłosował na `{}` piosenki.\n\
                Potrzebne jeszcze **{}** głosów, aby przegłosować.").format(ctx.author.mention, command, self.required(player, ctx.invoked_with) - len(attr)))

    async def connect_handler(self, ctx, msg):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.guild.me.voice:
            if ctx.guild.me.voice.channel == ctx.author.voice.channel:
                await msg.edit(content=_(ctx.lang, "Jestem już z tobą na kanale."))
                return await add_react(ctx.message, False)
        try:
            return await ctx.guild.me.move_to(ctx.author.voice.channel)
        except Exception:
            return await player.connect(ctx.author.voice.channel.id)
        else:
            return False

    @commands.command(aliases=['join'])
    async def connect(self, ctx):
        if not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś na żadnym kanale."))
            return await add_react(ctx.message, False)

        msg = await ctx.send(_(ctx.lang, "Łączenie z `{}`.").format(ctx.author.voice.channel.name))

        x = await self.connect_handler(ctx, msg)

        try:
            await ctx.guild.me.edit(deafen=True)
        except discord.HTTPException:
            pass

        if x == False:
            await msg.edit(content=_(ctx.lang, "Wystąpił błąd podczas łączenia."))
            return await add_react(ctx.message, False)

        await msg.edit(content=_(ctx.lang, "Połączono z `{}`.").format(ctx.author.voice.channel.name))
        return await add_react(ctx.message, True)

    @commands.command(aliases=['dc', 'stop'])
    async def disconnect(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś na żadnym kanale."))
            return await add_react(ctx.message, False)

        if not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na żadnym kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        await player.disconnect()
        await player.stop()

        await ctx.send(_(ctx.lang, "Rozłączono."))
        return await add_react(ctx.message, True)

    @commands.command(aliases=['p'])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def play(self, ctx, *, query: str):
        await ctx.trigger_typing()

        query = query.strip('<>')

        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.invoke(self.connect)

        if not player.dj:
            player.dj = ctx.author

        if not RURL.match(query):
            query = f'ytsearch:{query}'

        tracks = await self.bot.wavelink.get_tracks(query)

        if not tracks:
            SPOTIFY_RE = re.compile(r"(?:^|\W)spotify.com/track(?:$|\W)")
            if SPOTIFY_RE.findall(query):
                e = ctx.message.embeds[0].to_dict()
                tracks = await self.bot.wavelink.get_tracks(f"ytsearch:{e['description']}")

        if not tracks:
            await ctx.send(_(ctx.lang, "Nie znaleziono takiej piosenki."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if isinstance(tracks, wavelink.TrackPlaylist):
            for t in tracks.tracks:
                await player.queue.put(Track(t.id, t.info, ctx=ctx))

            await ctx.send(_(ctx.lang, "Dodano playliste `{}` z `{}` piosenkami do kolejki.").format(tracks.data["playlistInfo"]["name"], len(tracks.tracks)))
        else:
            track = tracks[0]
            await ctx.send(_(ctx.lang, "Dodano `{}` do kolejki.").format(track.title))
            await player.queue.put(Track(track.id, track.info, ctx=ctx))
            if not player.entries:
                player.current = track

        return await add_react(ctx.message, True)

    @play.before_invoke
    async def before_play(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        player.text_channel = ctx.channel

    @commands.command(aliases=['np'])
    async def now_playing(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player:
            return

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if not player.current:
            await ctx.send(_(ctx.lang, "Nic nie gra."))
            return await add_react(ctx.message, False)


        await ctx.send(_(ctx.lang, "Teraz gra: `{}`." + f" {'🔂' if player.repeat else ''}").format(player.current.title))

    @commands.command(name='pause')
    async def pause_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player:
            return

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if player.paused:
            await ctx.invoke(self.resume_)

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(_(ctx.lang, "{} zatrzymał piosenke jako administrator albo DJ.").format(ctx.author.mention))
            return await self.do_pause(ctx)

        await self.do_vote(ctx, player, 'pause')

    async def do_pause(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        player.paused = True
        await player.set_pause(True)

    @commands.command(name='resume')
    async def resume_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if not player.paused:
             await ctx.invoke(self.pause_)

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(_(ctx.lang, "{} wznowił piosenke jako administrator albo DJ.").format(ctx.author.mention))
            return await self.do_resume(ctx)

        await self.do_vote(ctx, player, 'resume')

    async def do_resume(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        await player.set_pause(False)

    @commands.command(name='skip')
    @commands.cooldown(5, 10, commands.BucketType.user)
    async def skip_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if (len(player.entries) + 1 if player.current else 0) == 0:
            await ctx.send(_(ctx.lang, "Nie ma już żadnych piosenek do przewinięcia."))
            return await add_react(ctx.message, False)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(_(ctx.lang, "{} przewinął piosenke jako administrator albo DJ.").format(ctx.author.mention))
            return await self.do_skip(ctx)

        await self.do_vote(ctx, player, 'skip')

    async def do_skip(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        await player.stop()
        player.repeat = False
        return await add_react(ctx.message, True)

    @commands.command(name='equalizer', aliases=['eq', 'seteq', 'set_eq'])
    async def set_eq(self, ctx, *, eq: str):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if eq.upper() not in player.equalizers:
            await ctx.send(_(ctx.lang, "`{}` nie jest prawidłowym equalizerem!\nSpróbuj `Flat, Boost, Metal, Piano`.").format(eq))
            return await add_react(ctx.message, False)

        await player.set_preq(eq)
        player.eq = eq.capitalize()
        await ctx.send(_(ctx.lang, "Equalizer został ustawiony na `{}`.").format(eq.capitalize()))
        return await add_react(ctx.message, True)

    @commands.command(aliases=['vol'])
    @commands.cooldown(1, 2, commands.BucketType.guild)
    async def volume(self, ctx, *, value: int=None):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if not 0 < value < 101:
            await ctx.send(_(ctx.lang, "Podaj liczbe od 1 do 100."))
            return await add_react(ctx.message, False)

        if not await self.has_perms(ctx, manage_guild=True) and player.dj.id != ctx.author.id:
            if (len(player.connected_channel.members) - 1) > 2:
                return await ctx.send(_(ctx.lang, "Jest za dużo osób na kanale, aby zmienić głośność muzyki.\n\
                    Możesz za to zmienić głośność indywidualnie klikając na mnie prawym przyciskiem myszy."))

        await player.set_volume(value)
        await ctx.send(_(ctx.lang, "Ustawiono głośność na **{}**%.").format(value))

    @commands.command(name='queue', aliases=['q', 'que'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def queue_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        upcoming = list(itertools.islice(player.entries, 0, 10))

        if not upcoming:
            await ctx.send(_(ctx.lang, "W kolejce nie ma obecnie żadnych utworów."))
            return await add_react(ctx.message, False)

        fmt = '\n'.join(f'**`{str(song)}`**' for song in upcoming)
        e = discord.Embed(description=fmt)
        e.set_author(name=_(ctx.lang, "Następne {} utworów").format(len(upcoming)), icon_url=self.bot.user.avatar_url)

        await ctx.send(embed=e)

    @commands.command(name='shuffle', aliases=['mix'])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def shuffle_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if len(player.entries) < 3:
            await ctx.send(_(ctx.lang, "Jest za mało utworów w playliście, aby je pomieszać."))
            return await add_react(ctx.message, False)

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(_(ctx.lang, "{} pomieszał piosenki w playliście jako administrator albo DJ.").format(ctx.author.mention))
            await add_react(ctx.message, True)
            return await self.do_shuffle(ctx)

        await self.do_vote(ctx, player, 'shuffle')

    async def do_shuffle(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        random.shuffle(player.queue._queue)

        player.update = True

    @commands.command(name='repeat', aliases=['l', 'loop'])
    async def repeat_(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected or not ctx.guild.me.voice:
            await ctx.send(_(ctx.lang, "Nie jestem na kanale."))
            return await add_react(ctx.message, False)

        if ctx.guild.me.voice.channel != ctx.author.voice.channel or not ctx.author.voice:
            await ctx.send(_(ctx.lang, "Nie jesteś ze mną na kanale."))
            return await add_react(ctx.message, False)

        if await self.has_perms(ctx, manage_guild=True):
            if player.repeat:
                text = "{} wyłączył powtarzanie utworu jako administrator albo DJ."

            else:
                text = "{} włączył powtarzanie utworu jako administrator albo DJ."

            await ctx.send(_(ctx.lang, text).format(ctx.author.mention))
            await add_react(ctx.message, True)
            return await self.do_repeat(ctx)

        await self.do_vote(ctx, player, 'repeat')

    async def do_repeat(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if player.repeat:
            player.repeat = None
        else:
            player.repeat = player.current

        player.update = True

    @commands.command(hidden=True, aliases=['minfo'])
    @commands.is_owner()
    async def music_info(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id)
        node = player.node

        used = humanize.naturalsize(node.stats.memory_used)
        total = humanize.naturalsize(node.stats.memory_allocated)
        free = humanize.naturalsize(node.stats.memory_free)
        cpu = node.stats.cpu_cores

        fmt = f'**WaveLink:** `{wavelink.__version__}`\n\n' \
              f'Connected to `{len(self.bot.wavelink.nodes)}` nodes.\n' \
              f'Best available Node `{self.bot.wavelink.get_best_node().__repr__()}`\n' \
              f'`{len(self.bot.wavelink.players)}` players are distributed on nodes.\n' \
              f'`{node.stats.players}` players are distributed on server.\n' \
              f'`{node.stats.playing_players}` players are playing on server.\n\n' \
              f'Server Memory: `{used}/{total}` | `({free} free)`\n' \
              f'Server CPU: `{cpu}`\n\n' \
              f'Server Uptime: `{datetime.timedelta(milliseconds=node.stats.uptime)}`'
        await ctx.send(fmt)

def setup(bot):
    bot.add_cog(Music(bot))
