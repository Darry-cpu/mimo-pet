# -*- coding: utf-8 -*-
"""余额预警：盯着邮箱里小米自己发来的"余额提醒"邮件。

小米没有开放查余额的接口（实测 `/v1/credits` 等路径在 api.xiaomimimo.com 上都是 404，
连他们自己的客户端也只在调用失败时用正则去认"余额不足"这句话），所以真正靠谱的
提前信号是官方发到账号邮箱的那封提醒邮件。

这个模块刻意做得很保守：
  * **只读**：select(readonly=True) + BODY.PEEK，绝不标已读、不移动、不删除任何邮件；
  * 只看配置里指定的发件人/关键词命中的那几封，而且**只读邮件头**（发件人/主题/日期），不读正文；
  * 任何异常都被吞掉、下次再试，桌宠本身绝不受影响；
  * 授权码只存在本机配置文件里，不进日志、不进仓库。
"""
from __future__ import annotations

import email
import email.header
import imaplib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MimoPet"
CONFIG_PATH = DATA_DIR / "alert.ini"
STATE_PATH = DATA_DIR / "alert_seen.json"

# 命中邮件后气泡上显示的话（真实邮件主题另外记进日志，方便排查）
ALERT_TEXT = "余额预警：小米发来一封提醒邮件，提醒你的mimo余额已经低于两块，请尽快充值！！！"

TEMPLATE = """\
Mimo 桌宠 · 余额预警                          ← 只需要填两行，其它都不用管

怎么填（约 2 分钟）：
  1. 电脑浏览器打开邮箱网页版 → 设置 → 账号
  2. 找到「IMAP/SMTP 服务」并开启
     （QQ 邮箱会要求用手机发一条短信验证；开启后显示一串 16 位授权码，
       形如 abcd efgh ijkl mnop —— 这就是下面要填的东西，不是登录密码）
  3. 把「邮箱地址」和「授权码」填到下面两行的等号后面
  4. 保存这个文件，然后右键桌宠 →「余额预警」→「测试连接」
     她要是说“连上了”，就成功了

邮箱 =
授权码 =

（什么邮箱都行：QQ / 163 / 126 / Gmail / Outlook，服务器地址会自动识别。
  这个文件只在你自己的电脑上，不会上传、也不会进代码仓库。
  不想用了，把授权码那行清空即可，或者去邮箱里关闭 IMAP。）
"""

# 按邮箱域名自动认服务器，省得你填
SERVERS = {
    "qq.com": ("imap.qq.com", 993),
    "vip.qq.com": ("imap.qq.com", 993),
    "foxmail.com": ("imap.qq.com", 993),
    "163.com": ("imap.163.com", 993),
    "126.com": ("imap.126.com", 993),
    "yeah.net": ("imap.yeah.net", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "sina.com": ("imap.sina.com", 993),
    "aliyun.com": ("imap.aliyun.com", 993),
    "139.com": ("imap.139.com", 993),
}


def _derive_server(address: str) -> tuple[str, int]:
    domain = address.split("@")[-1].strip().lower()
    return SERVERS.get(domain, ("imap." + domain, 993) if domain else ("", 993))


# 配置文件里认识这些写法（大小写、中英文都行）
_KEYS = {
    "邮箱": "user", "邮箱地址": "user", "email": "user", "user": "user", "账号": "user",
    "授权码": "auth_code", "密码": "auth_code", "auth_code": "auth_code", "authcode": "auth_code",
    "服务器": "server", "server": "server", "imap": "server",
    "端口": "port", "port": "port",
    "关键词": "keywords", "关键字": "keywords", "keywords": "keywords",
    "发件人": "from_contains", "发件人含": "from_contains", "from": "from_contains",
    "间隔": "check_every", "检查间隔": "check_every", "minutes": "check_every",
    "充值页": "page", "网页": "page", "page": "page",
    "动作": "pose", "pose": "pose",
    "收件夹": "folder", "folder": "folder",
}


@dataclass
class AlertConfig:
    server: str = "imap.qq.com"
    port: int = 993
    user: str = ""
    auth_code: str = ""
    folder: str = "INBOX"
    scan_last: int = 25
    check_every: int = 10
    # 只认「Xiaomi MiMo 开放平台」发来的邮件：显示名匹配，或它自己的域名
    from_contains: list[str] = field(
        default_factory=lambda: ["xiaomi mimo 开放平台", "xiaomimimo"])
    subject_contains: list[str] = field(
        default_factory=lambda: ["余额", "预警", "欠费", "充值", "额度", "用量"])
    page: str = "https://platform.xiaomimimo.com/"
    pose: str = "panic"
    enabled: bool = True

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.user and self.auth_code)


def _split(value: str) -> list[str]:
    return [v.strip().lower() for v in value.replace("，", ",").split(",") if v.strip()]


def ensure_config(path: Path = CONFIG_PATH) -> Path:
    """Write the template the first time; never touch an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(TEMPLATE, encoding="utf-8")
        logging.info("alert config created at %s", path)
    return path


def load_config(path: Path = CONFIG_PATH) -> AlertConfig:
    """Parse the two-line config. Anything missing falls back to a sane default,
    so a half-filled file never breaks the pet."""
    ensure_config(path)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        logging.warning("alert config unreadable, alerts stay off", exc_info=True)
        return AlertConfig(enabled=False)

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().strip("　")
        if not line or line[0] in "#；;" or "=" not in line:
            continue
        key, _, val = line.partition("=")
        name = _KEYS.get(key.strip().lower())
        if name:
            values[name] = val.strip()

    cfg = AlertConfig()
    cfg.user = values.get("user", "").strip()
    cfg.auth_code = values.get("auth_code", "").replace(" ", "").strip()
    server, port = _derive_server(cfg.user)
    cfg.server = values.get("server", "").strip() or server
    try:
        cfg.port = int(values.get("port") or port)
    except ValueError:
        cfg.port = port
    cfg.folder = values.get("folder", "").strip() or "INBOX"
    if values.get("keywords"):
        cfg.subject_contains = _split(values["keywords"])
    if values.get("from_contains"):
        cfg.from_contains = _split(values["from_contains"])
    try:
        cfg.check_every = max(1, int(values.get("check_every") or cfg.check_every))
    except ValueError:
        pass
    if values.get("page"):
        cfg.page = values["page"].strip()
    if values.get("pose"):
        cfg.pose = values["pose"].strip()
    return cfg


def _header(value) -> str:
    try:
        parts = email.header.decode_header(value or "")
        out = []
        for text, enc in parts:
            out.append(text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text)
        return "".join(out)
    except Exception:                                                  # noqa: BLE001
        return str(value or "")


def _load_seen() -> set[str]:
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("seen", []))
    except Exception:                                                  # noqa: BLE001
        return set()


def _save_seen(seen: set[str]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"seen": sorted(seen)[-400:]}, ensure_ascii=False),
                              encoding="utf-8")
    except Exception:                                                  # noqa: BLE001
        logging.debug("could not save alert state", exc_info=True)


@dataclass
class Match:
    uid: str
    subject: str
    sender: str


def scan(cfg: AlertConfig, timeout: int = 25) -> list[Match]:
    """Read-only scan of the newest few messages; returns matching header info."""
    conn = imaplib.IMAP4_SSL(cfg.server, cfg.port, timeout=timeout)
    try:
        conn.login(cfg.user, cfg.auth_code)
        typ, data = conn.select(cfg.folder, readonly=True)
        if typ != "OK":
            raise RuntimeError("cannot open folder %s: %s" % (cfg.folder, data))
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError("uid search failed: %s" % (data,))
        uids = (data[0] or b"").split()[-cfg.scan_last:]
        if not uids:
            return []
        # BODY.PEEK keeps everything unread
        typ, data = conn.uid("fetch", b",".join(uids),
                             "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK":
            raise RuntimeError("uid fetch failed: %s" % (data,))
    finally:
        try:
            conn.logout()
        except Exception:                                              # noqa: BLE001
            pass

    matches: list[Match] = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        raw_meta, raw_head = item[0], item[1]
        uid = ""
        meta = raw_meta.decode("utf-8", "replace") if isinstance(raw_meta, bytes) else str(raw_meta)
        for token in meta.split():
            if token.isdigit() and not uid:
                uid = token
                break
        msg = email.message_from_bytes(raw_head if isinstance(raw_head, bytes) else raw_head.encode())
        sender = _header(msg.get("From")).lower()
        subject = _header(msg.get("Subject")).strip()
        low = subject.lower()
        if cfg.from_contains and not any(k in sender for k in cfg.from_contains):
            continue
        if cfg.subject_contains and not any(k in low for k in cfg.subject_contains):
            continue
        matches.append(Match(uid=uid, subject=subject, sender=sender))
    return matches


def test_connection(cfg: AlertConfig) -> str:
    """One-off check used by the right-click menu; returns a sentence for the bubble."""
    if not cfg.enabled:
        return "余额预警已关闭（配置文件里 enabled = no）"
    if not cfg.auth_code:
        return "还没填授权码：右键我 -> 余额预警 -> 打开配置文件"
    try:
        hits = scan(cfg)
    except imaplib.IMAP4.error as exc:
        text = str(exc)
        if "AUTH" in text.upper() or "LOGIN" in text.upper() or "authorization" in text.lower():
            return "连不上：授权码不对，或者 IMAP 没开启"
        return "连不上邮箱：%s" % text[:40]
    except Exception as exc:                                           # noqa: BLE001
        return "连不上邮箱：%s" % type(exc).__name__
    if hits:
        return "连上了！最近有 %d 封像余额提醒的邮件" % len(hits)
    return "连上了！最近 %d 封里没有余额提醒" % cfg.scan_last


class EmailWatcher:
    """Poll the mailbox on a background thread; hand any new hit to the pet.

    The config is re-read every cycle, so filling in the auth code takes effect
    within a minute without restarting the pet.
    """

    def __init__(self, load_config, on_alert) -> None:
        self._load = load_config
        self.on_alert = on_alert
        self._seen = _load_seen()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _sleep(self, seconds: float) -> None:
        waited = 0.0
        while waited < seconds and not self._stop:
            time.sleep(1.0)
            waited += 1.0

    def run(self) -> None:
        announced_off = announced_on = False
        while not self._stop:
            cfg = self._load()
            if not cfg.usable:
                if not announced_off:
                    logging.info("余额预警未配置（缺邮箱或授权码），先跳过；填好后一分钟内自动生效")
                    announced_off = True
                announced_on = False
                self._sleep(60)
                continue
            if not announced_on:
                logging.info("余额预警已启动：每 %d 分钟看一次 %s 的 %s",
                             cfg.check_every, cfg.user, cfg.folder)
                announced_on = True
                announced_off = False
            try:
                fresh = [m for m in scan(cfg) if m.uid and m.uid not in self._seen]
                for m in reversed(fresh):
                    self._seen.add(m.uid)
                    logging.info("余额预警邮件命中: %s", (m.subject or "(无主题)")[:60])
                    self.on_alert(ALERT_TEXT)
                if fresh:
                    _save_seen(self._seen)
            except Exception as exc:                                   # noqa: BLE001
                logging.info("余额预警这次没查成（下次再试）：%s", exc)
            self._sleep(cfg.check_every * 60)
