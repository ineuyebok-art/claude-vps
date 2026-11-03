"""Приём файлов: имена, ссылки на диски, загрузка."""
import email.message
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import config, helper

ALLOWED_HOSTS = {
    "google": {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"},
    "yandex": {"disk.yandex.ru", "disk.yandex.com", "disk.yandex.net", "disk.yandex.by",
               "disk.yandex.kz", "disk.360.yandex.ru", "disk.360.yandex.com", "yadi.sk"},
    "dropbox": {"dropbox.com", "www.dropbox.com"},
}
# Куда диски перенаправляют на сам файл. Только суффиксы их собственных доменов.
REDIRECT_SUFFIXES = (".googleusercontent.com", ".dropboxusercontent.com", ".dropbox.com",
                     ".yandex.net", ".yandex.ru", ".yandex.com", ".google.com")
URL_RE = re.compile(r"https?://[^\s<>\"'»)\]]+")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


class LinkError(Exception):
    pass


def sanitize_name(name, default="file"):
    """Безопасное имя: без путей, скрытых файлов и символов, запрещённых в Windows."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r'[<>:"|?*\s]+', "_", name)
    name = name.lstrip(".~$-_ ").rstrip(". ")
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    stem, suffix = stem[:120], suffix[:20]
    if stem.upper() in WINDOWS_RESERVED:
        stem = "_" + stem
    if not stem:
        return default
    return f"{stem}.{suffix}" if suffix else stem


def host_of(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return ""
    return (parts.hostname or "").lower()


def link_kind(url):
    host = host_of(url)
    for kind, hosts in ALLOWED_HOSTS.items():
        if host in hosts:
            return kind
    return None


def redirect_allowed(url):
    host = host_of(url)
    return bool(host) and (any(host in hosts for hosts in ALLOWED_HOSTS.values())
                           or host.endswith(REDIRECT_SUFFIXES))


def find_links(text):
    """Делит ссылки текста на разрешённые и чужие."""
    allowed, foreign = [], []
    for url in URL_RE.findall(text or ""):
        url = url.rstrip(".,;:!?")
        (allowed if link_kind(url) else foreign).append(url)
    return allowed, foreign


def direct_url(url):
    """Переводит ссылку диска в прямую ссылку на скачивание."""
    kind = link_kind(url)
    parts = urllib.parse.urlsplit(url)
    if kind == "google":
        doc = re.search(r"/(document|spreadsheets|presentation)/d/([\w-]+)", parts.path)
        if doc:
            fmt = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pptx"}[doc.group(1)]
            return f"https://docs.google.com/{doc.group(1)}/d/{doc.group(2)}/export?format={fmt}"
        match = re.search(r"/file/d/([\w-]+)", parts.path) or re.search(r"[?&]id=([\w-]+)", url)
        if not match:
            raise LinkError("Папки Google Диска не поддерживаются: нужна ссылка на файл.")
        return f"https://drive.usercontent.google.com/download?id={match.group(1)}&export=download&confirm=t"
    if kind == "dropbox":
        query = dict(urllib.parse.parse_qsl(parts.query))
        query["dl"] = "1"
        return urllib.parse.urlunsplit(("https", parts.netloc, parts.path, urllib.parse.urlencode(query), ""))
    if kind == "yandex":
        return ("https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key="
                + urllib.parse.quote(url, safe=""))
    raise LinkError("Эта ссылка не с разрешённого диска.")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not redirect_allowed(newurl):
            raise LinkError("Диск перенаправил на чужой адрес, скачивание остановлено.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_Redirects)


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 claude-vps"})
    try:
        return _opener().open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        raise LinkError(f"Диск ответил ошибкой {exc.code}. Проверь, что файл открыт по ссылке.") from None
    except urllib.error.URLError:
        raise LinkError("Не удалось соединиться с диском.") from None


def _yandex_href(url):
    with _open(url) as resp:
        href = json.load(resp).get("href", "")
    if not redirect_allowed(href):
        raise LinkError("Яндекс Диск вернул неожиданный адрес.")
    return href


def _filename(resp, url):
    message = email.message.Message()
    message["Content-Disposition"] = resp.headers.get("Content-Disposition", "")
    name = message.get_filename()
    if name:
        return name
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])


class _Limited:
    """Обрывает поток, если файл оказался больше лимита."""

    def __init__(self, stream, limit):
        self.stream, self.limit, self.total = stream, limit, 0

    def read(self, size):
        chunk = self.stream.read(size)
        self.total += len(chunk)
        if self.total > self.limit:
            raise LinkError("Файл больше 2 ГБ.")
        return chunk


def download_link(url):
    """Скачивает файл по разрешённой ссылке в ~/inbox. Возвращает путь на сервере."""
    if not link_kind(url):
        raise LinkError("Скачиваю только с Google Диска, Яндекс Диска и Dropbox.")
    target = direct_url(url)
    if link_kind(url) == "yandex":
        target = _yandex_href(target)
    with _open(target) as resp:
        size = int(resp.headers.get("Content-Length") or 0)
        if size > config.LINK_LIMIT:
            raise LinkError("Файл больше 2 ГБ.")
        if resp.headers.get_content_type() == "text/html":
            raise LinkError("Вместо файла диск отдал страницу. Открой доступ по ссылке.")
        return helper.write_inbox(sanitize_name(_filename(resp, resp.geturl()), "download"),
                                  _Limited(resp, config.LINK_LIMIT))
