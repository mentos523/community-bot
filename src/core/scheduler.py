"""主调度循环：每 60 秒拉各社区轻量列表，按梯度决定是否进详情。

R2-S1：每个社区独立线程轮询（ThreadPoolExecutor），单个社区 hang 住
不拖死全局。共享状态（state/_seen_ids）用 RLock 保护。
R2-S2：state.json 定期清理 + dirty 标记，无变化不写盘。
"""
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src.core.base import AuthError
from src.core.tiers import get_tier, tier_due
from src.core.generator import generate_reply

POLL_INTERVAL = 60  # 主循环：每 60 秒拉一次轻量列表（不产生浏览量）
SEEN_IDS_CAP = 5000  # S3：已处理消息 ID 去重集合上限（防内存膨胀）
MAX_WORKERS = 8  # R2-S1：社区轮询线程池上限
PRUNE_DAYS = 30  # R2-S2：archived 且超 N 天无活动的会话从 state 删除


def _parse_iso(s: str | None):
    """解析 ISO 时间，失败返回 None。"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Scheduler:
    def __init__(self, config: dict, adapters: dict, model_manager,
                 state_path: str, logger):
        self.config = config
        self.adapters = adapters          # {community_name: adapter}
        self.mm = model_manager
        self.state_path = state_path
        self.log = logger
        # R2-S1：所有共享可变状态用同一把 RLock 保护
        self._lock = threading.RLock()
        self.state = self._load_state()
        self._dirty = False  # R2-S2：state 有变化才写盘
        # S3：纵深防御——已处理过的消息 ID（本进程回复过的也在这里），
        # 即使插件没提供 bot_user_id，也不会重复处理/自回环
        self._seen_ids: set = set()
        self._seen_order: list = []
        self._bot_id_warned: set = set()  # 已警告过缺少 bot_user_id 的社区
        # S3：本进程发出的回复内容指纹，用于识别"自身回声"（防自回环）
        self._sent_fingerprints: set = set()
        self._sent_fp_order: list = []

    # ---- 状态持久化 ----

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            # R2-M9：损坏时备份再重置，避免水位全丢无声无息
            try:
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                bad = f"{self.state_path}.corrupt.{ts}"
                os.replace(self.state_path, bad)
                self.log.error(f"state.json 损坏（{e}），已备份为 {bad}，状态重置")
            except Exception as be:
                self.log.error(f"state.json 损坏且备份失败: {be}")
            return {}

    def _mark_dirty(self):
        self._dirty = True

    def _save_state(self):
        # R2-S2：无变化不写盘
        with self._lock:
            if not self._dirty:
                return
            try:
                os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
                tmp = self.state_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, indent=2)
                # U10+N3：含 token/水位，临时文件建好立刻设 600，缩小权限窗口
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                # U10：已存在文件保持原权限（只对新建文件确保 600）
                is_new = not os.path.exists(self.state_path)
                os.replace(tmp, self.state_path)
                if is_new:
                    try:
                        os.chmod(self.state_path, 0o600)
                    except OSError:
                        pass
                self._dirty = False
            except Exception as e:
                self.log.warning(f"保存状态失败: {e}")

    def _prune_state(self):
        """R2-S2：每天清理一次——删除 archived 且超 PRUNE_DAYS 无活动的会话。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            meta = self.state.setdefault("communities", {})  # noqa: F841
            m = self.state.setdefault("_meta", {})
            if m.get("last_prune") == today:
                return
            now = datetime.now(timezone.utc)
            removed = 0
            comms = self.state.get("communities") or {}
            for comm_name in list(comms.keys()):
                convs = comms.get(comm_name) or {}
                if not isinstance(convs, dict):
                    continue
                for conv_id in list(convs.keys()):
                    rec = convs.get(conv_id) or {}
                    if rec.get("tier") != "archived":
                        continue
                    la = _parse_iso(rec.get("last_activity"))
                    if la and (now - la).days > PRUNE_DAYS:
                        del convs[conv_id]
                        removed += 1
            m["last_prune"] = today
            if removed:
                self._mark_dirty()
                self.log.info(f"state 清理：删除 {removed} 个过期归档会话")

    def _conv_rec(self, comm_name: str, conv_id: str) -> dict:
        with self._lock:
            comms = self.state.setdefault("communities", {})
            convs = comms.setdefault(comm_name, {})
            key = str(conv_id)
            if key not in convs:
                convs[key] = {}
                self._mark_dirty()
            return convs[key]

    def _set_rec(self, rec: dict, key: str, value):
        """R2-S2：只有值真正变化时才写 + 标 dirty。"""
        with self._lock:
            if rec.get(key) != value:
                rec[key] = value
                self._mark_dirty()

    def _remember_seen(self, msg_id: str):
        """记录已处理的消息 ID，超上限时淘汰最旧的。"""
        with self._lock:
            if msg_id in self._seen_ids:
                return
            self._seen_ids.add(msg_id)
            self._seen_order.append(msg_id)
            if len(self._seen_order) > SEEN_IDS_CAP:
                old = self._seen_order.pop(0)
                self._seen_ids.discard(old)

    # ---- 主循环 ----

    def run(self):
        self.log.info("调度器启动，开始轮询")
        while True:
            try:
                self.poll_once()
            except Exception:
                self.log.exception("轮询主循环异常")
            time.sleep(POLL_INTERVAL)

    def poll_once(self):
        # R2-M2：显式 null 时用 or 兜底，避免 for None 崩溃
        rules = self.config.get("rules") or {}
        tiers_cfg = self.config.get("tiers") or {}
        intervals = {
            "hot": tiers_cfg.get("hot_interval", 60),
            "warm": tiers_cfg.get("warm_interval", 1800),
            "cold": tiers_cfg.get("cold_interval", 604800),
        }
        default_model = self.config.get("default_model")
        comms = self.config.get("communities") or []
        # R2-S1：每个社区独立线程轮询，单个 hang 住不拖死全局
        workers = max(1, min(len(comms), MAX_WORKERS))
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="poll") as ex:
            futures = [ex.submit(self._poll_one, comm, rules, intervals,
                                 default_model)
                       for comm in comms]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    self.log.exception("社区轮询线程异常")
        self._prune_state()
        self._save_state()

    def _poll_one(self, comm, rules: dict, intervals: dict,
                  default_model: str | None):
        """R2-M1：单个社区的完整处理（含配置校验），异常只影响本社区。"""
        try:
            if not isinstance(comm, dict):
                self.log.error(f"社区配置条目不是 dict，已跳过: {comm!r:.80}")
                return
            name = comm.get("name") or "<unnamed>"
            if not comm.get("name"):
                self.log.error("社区配置缺少 name，已跳过")
                return
            if not comm.get("enabled", True):
                return
            adapter = self.adapters.get(comm.get("name"))
            if not adapter:
                self.log.error(f"[{name}] 没有加载的适配器，跳过")
                return
            self._poll_community(comm, adapter, rules, intervals, default_model)
        except Exception:
            self.log.exception(f"[{comm.get('name') if isinstance(comm, dict) else comm}] 社区轮询异常")

    # ---- 带认证重试的调用 ----

    def _call_with_reauth(self, adapter, comm_name: str, fn, *args):
        """调用适配器方法，401 时自动重登一次再试。"""
        try:
            return fn(*args)
        except AuthError:
            self.log.warning(f"[{comm_name}] 认证失效，尝试重新登录")
            try:
                if adapter.refresh_auth():
                    self.log.info(f"[{comm_name}] 重登成功，重试")
                    return fn(*args)
            except AuthError:
                pass
            self.log.error(f"[{comm_name}] 重登失败，跳过本轮")
            return None

    # ---- 社区轮询 ----

    def _poll_community(self, comm: dict, adapter, rules: dict,
                        intervals: dict, default_model: str | None):
        name = comm["name"]
        updates = self._call_with_reauth(adapter, name, adapter.fetch_updates)
        if updates is None:
            return
        # 按会话分组，取每会话的最新活动时间（M1：转 datetime 后比较，
        # 避免 "Z" 与 "+00:00" 字符串混用导致的误判）
        convs: dict[str, dict] = {}
        for m in updates:
            c = convs.setdefault(m.conversation_id, {"msgs": [], "latest": None, "latest_dt": None})
            c["msgs"].append(m)
            dt = _parse_iso(m.created_at)
            if dt and (c["latest_dt"] is None or dt > c["latest_dt"]):
                c["latest_dt"] = dt
                c["latest"] = m.created_at
        now_iso = _now_iso()
        for conv_id, c in convs.items():
            rec = self._conv_rec(name, conv_id)
            old_activity = rec.get("last_activity")
            old_dt = _parse_iso(old_activity)
            latest = c["latest"]
            latest_dt = c["latest_dt"]
            # 新会话或有新活动：直接升 Hot，立即检查
            # （旧时间戳损坏时不误判为新活动，避免会话锁死 hot）
            if old_activity is None:
                new_activity = True
            elif old_dt is None:
                new_activity = False
            else:
                new_activity = latest_dt is not None and latest_dt > old_dt
            if latest:
                self._set_rec(rec, "last_activity", latest)
            if new_activity:
                self._set_rec(rec, "tier", "hot")
                self._set_rec(rec, "last_check", now_iso)
                self._check_conversation(comm, adapter, conv_id, rec, rules, default_model)
                continue
            # 无新活动：按梯度决定是否到期
            self._set_rec(rec, "tier", get_tier(rec.get("last_activity")))
            if not tier_due(rec["tier"], rec.get("last_check"), intervals):
                continue
            self._set_rec(rec, "last_check", now_iso)
            self._check_conversation(comm, adapter, conv_id, rec, rules, default_model)

    def _check_conversation(self, comm: dict, adapter, conv_id: str,
                            rec: dict, rules: dict, default_model: str | None):
        name = comm["name"]
        detail = self._call_with_reauth(adapter, name, adapter.fetch_detail, conv_id)
        if not detail:
            return
        # S3：排除机器人自己发的。插件没提供 bot_user_id 时打警告，
        # 靠已处理 ID 去重 + 自发内容指纹做第二/三道防线
        bot_id = adapter.get_info().get("bot_user_id")
        if not bot_id and name not in self._bot_id_warned:
            self._bot_id_warned.add(name)
            self.log.warning(f"[{name}] 插件未提供 bot_user_id，自回环过滤依赖消息去重")
        msgs = sorted(
            [m for m in detail if _parse_iso(m.created_at)],
            key=lambda m: _parse_iso(m.created_at),
        )
        if not msgs:
            return
        last_seen = _parse_iso(rec.get("last_seen_at"))
        for m in msgs:
            if last_seen is not None and _parse_iso(m.created_at) <= last_seen:
                continue
            if m.id in self._seen_ids:
                self._set_rec(rec, "last_seen_at", m.created_at)
                continue
            if bot_id and m.author_id == bot_id:
                self._set_rec(rec, "last_seen_at", m.created_at)  # 自己的消息，水位直接过
                continue
            if self._is_own_echo(conv_id, m):
                self.log.info(f"[{name}] 跳过疑似自身发出的回复 {m.id}")
                self._set_rec(rec, "last_seen_at", m.created_at)
                continue
            try:
                handled = self._maybe_reply(comm, adapter, m, rules, default_model)
            except Exception:
                self.log.exception(f"[{name}] 回复 {conv_id} 异常")
                handled = False
            if not handled:
                break  # S4：未处理成功不推进水位，下轮重试；本轮不再处理更后面的
            self._remember_seen(m.id)
            self._set_rec(rec, "last_seen_at", m.created_at)

    def _fingerprint(self, conv_id: str, text: str) -> tuple:
        """S3：自发回复的内容指纹（会话 ID + 正文前 200 字哈希）。"""
        return (str(conv_id),
                hashlib.sha256(text.strip()[:200].encode("utf-8")).hexdigest())

    def _remember_fingerprint(self, conv_id: str, text: str):
        with self._lock:
            fp = self._fingerprint(conv_id, text)
            self._sent_fingerprints.add(fp)
            self._sent_fp_order.append(fp)
            if len(self._sent_fp_order) > SEEN_IDS_CAP:
                self._sent_fingerprints.discard(self._sent_fp_order.pop(0))

    def _is_own_echo(self, conv_id: str, msg) -> bool:
        """ incoming 消息内容与本进程发过的回复一致 → 判定为自身回声。"""
        content = (msg.content or "").strip()
        if not content:
            return False
        return self._fingerprint(conv_id, content) in self._sent_fingerprints

    # ---- 回复决策 ----

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_rate_limit(self, comm_name: str, conv_id: str, rules: dict) -> bool:
        """频率控制：每天总量 + 每会话量。"""
        with self._lock:
            today = self._today()
            c = self.state.setdefault("counters", {}).setdefault(comm_name, {})
            if c.get("date") != today:
                c.clear()
                c["date"] = today
                self._mark_dirty()
            if c.get("total", 0) >= rules.get("max_replies_per_day", 20):
                return False
            convs = c.setdefault("convs", {})
            if convs.get(str(conv_id), 0) >= rules.get("max_replies_per_discussion_per_day", 2):
                return False
            return True

    def _bump_rate_limit(self, comm_name: str, conv_id: str):
        with self._lock:
            c = self.state["counters"][comm_name]
            c["total"] = c.get("total", 0) + 1
            c.setdefault("convs", {})[str(conv_id)] = c["convs"].get(str(conv_id), 0) + 1
            self._mark_dirty()

    def _maybe_reply(self, comm: dict, adapter, msg, rules: dict,
                     default_model: str | None) -> bool:
        """尝试回复一条消息。

        返回 True=已处理（已回复或明确判定跳过），False=未处理（下轮重试）。
        S4：只有返回 True 时调用方才推进水位线。
        """
        name = comm["name"]
        content = (msg.content or "").strip()
        author = msg.author_name or msg.author_id or ""
        if not self._check_rate_limit(name, msg.conversation_id, rules):
            self.log.info(f"[{name}] 触发频率限制，跳过 {msg.conversation_id}")
            return False
        # 特定用户每次都回；其他用户走四条门槛
        always = (author in rules.get("always_reply_users", [])
                  or msg.author_id in rules.get("always_reply_users", []))
        if not always:
            if len(content) < rules.get("min_question_length", 5):
                return True  # 明确跳过：太短不值得回
            for kw in rules.get("skip_keywords", []):
                if kw and kw in content:
                    self.log.info(f"[{name}] 命中跳过关键词，跳过")
                    return True  # 明确跳过
        model_name = comm.get("model") or default_model
        if not model_name:
            self.log.error("未配置 default_model，无法生成回复")
            return False
        style = rules.get("reply_style", {})
        prompts_cfg = self.config.get("prompts") or {}
        text, reason = generate_reply(
            self.mm, model_name, content,
            bot_name=(comm.get("bot_name") or "").strip() or None,  # U15+N4：三级回退，纯空格视为未设置
            max_length=style.get("max_length", 0),
            options=self.mm.get_options(model_name),
            logger=self.log,
            prompts_cfg=prompts_cfg,
        )
        # R2-M6：主模型调用失败时，用备用模型重试一次
        if not text and reason.startswith("模型调用失败"):
            fb = self.config.get("fallback_model")
            if fb and fb != model_name:
                self.log.warning(f"[{name}] 主模型 {model_name} 失败，尝试备用模型 {fb}")
                text, reason = generate_reply(
                    self.mm, fb, content,
                    bot_name=(comm.get("bot_name") or "").strip() or None,  # U15+N4：三级回退，纯空格视为未设置
                    max_length=style.get("max_length", 0),
                    options=self.mm.get_options(fb),
                    logger=self.log,
                    prompts_cfg=prompts_cfg,
                )
        if not text:
            self.log.warning(f"[{name}] {msg.conversation_id} 生成失败: {reason}")
            return False
        ok = self._call_with_reauth(adapter, name, adapter.reply,
                                    msg.conversation_id, text)
        if not ok:
            self.log.warning(f"[{name}] 回复 {msg.conversation_id} 失败")
            return False
        # M3：回复后 GET 验证（至少非空）。验证请求本身异常则视为成功，
        # 避免误判导致重复发送
        try:
            detail = self._call_with_reauth(adapter, name, adapter.fetch_detail,
                                            msg.conversation_id)
        except Exception:
            detail = None
            self.log.warning(f"[{name}] 回复后验证异常，视为成功")
        if detail is not None and not detail:
            self.log.warning(f"[{name}] 回复后验证为空，视为失败，下轮重试")
            return False
        self._bump_rate_limit(name, msg.conversation_id)
        self._remember_fingerprint(msg.conversation_id, text)
        self.log.info(f"[{name}] 已回复 {msg.conversation_id}（作者 {author}）")
        return True
