import requests


def _request(url, **kwargs):
    headers = kwargs.get("headers") or None
    params = kwargs.get("params") or None

    r = requests.get(url, params=params, headers=headers)
    return r if r.status_code == 200 else None


def get(url, **kwargs):
    r = _request(url, **kwargs)
    return r.json() if r is not None else None
