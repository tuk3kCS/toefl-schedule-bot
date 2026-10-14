from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


IIG_PAGE_URL = "https://online.iigvietnam.com/lich-thi-tieng-anh"
DEFAULT_IIG_BASE_URL = "https://online.iigvietnam.com/api/iig"
STATE_VERSION = 1
VIETNAM_TZ = timezone(timedelta(hours=7))


def env(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} phải là true/false, 1/0, yes/no hoặc on/off")


@dataclass(frozen=True)
class Config:
    iig_base_url: str = DEFAULT_IIG_BASE_URL
    exam_code: str = "TOEFL-ITP-OL"
    exam_name: str = "TOEFL ITP - trên máy tính"
    area_code: str = "HANOI"
    area_name: str = "Hà Nội"
    lookahead_days: int = 60
    only_open: bool = False
    notify_on_first_run: bool = False
    max_seen_fingerprints: int = 500
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_environment(cls) -> "Config":
        lookahead_days = int(env("IIG_LOOKAHEAD_DAYS", "60"))
        if not 1 <= lookahead_days <= 60:
            raise ValueError("IIG_LOOKAHEAD_DAYS phải nằm trong khoảng 1..60 (giới hạn API IIG)")

        max_seen = int(env("MAX_SEEN_FINGERPRINTS", "500"))
        if max_seen < 20:
            raise ValueError("MAX_SEEN_FINGERPRINTS phải >= 20")

        return cls(
            iig_base_url=env("IIG_BASE_URL", DEFAULT_IIG_BASE_URL).rstrip("/"),
            exam_code=env("IIG_EXAM_CODE", "TOEFL-ITP-OL"),
            exam_name=env("IIG_EXAM_NAME", "TOEFL ITP - trên máy tính"),
            area_code=env("IIG_AREA_CODE", "HANOI"),
            area_name=env("IIG_AREA_NAME", "Hà Nội"),
            lookahead_days=lookahead_days,
            only_open=env_bool("IIG_ONLY_OPEN", False),
            notify_on_first_run=env_bool("NOTIFY_ON_FIRST_RUN", False),
            max_seen_fingerprints=max_seen,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )

    def filter_signature(self) -> dict[str, Any]:
        return {
            "examCode": self.exam_code,
            "examName": self.exam_name,
            "areaCode": self.area_code,
            "areaName": self.area_name,
            "lookaheadDays": self.lookahead_days,
            "onlyOpen": self.only_open,
        }


class IIGClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Referer": IIG_PAGE_URL,
                "User-Agent": "iig-toefl-itp-hanoi-monitor/1.0 (+GitHub Actions)",
            }
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.config.iig_base_url}/{path.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=(10, 30))
        response.raise_for_status()
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError(f"IIG không trả JSON tại {response.url}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Phản hồi IIG không đúng dạng object tại {response.url}")
        status_code = payload.get("statusCode")
        if status_code is not None and int(status_code) != 200:
            raise RuntimeError(f"IIG API lỗi {status_code}: {payload.get('message', 'không rõ lỗi')}")
        return payload

    @staticmethod
    def _resolve_catalog_item(
        items: Iterable[dict[str, Any]], *, code: str, name: str, kind: str
    ) -> dict[str, Any]:
        materialized = list(items)
        code_matches = [item for item in materialized if str(item.get("code", "")).casefold() == code.casefold()]
        if len(code_matches) == 1:
            return code_matches[0]

        target_name = normalize_text(name)
        name_matches = [item for item in materialized if normalize_text(str(item.get("name", ""))) == target_name]
        if len(name_matches) == 1:
            return name_matches[0]

        available = ", ".join(
            f"{item.get('code', '?')}={item.get('name', '?')}" for item in materialized
        )
        raise RuntimeError(
            f"Không tìm thấy duy nhất {kind} code={code!r}, name={name!r}. Danh mục hiện có: {available}"
        )

    def resolve_filters(self) -> tuple[dict[str, Any], dict[str, Any]]:
        exams = self._get("Catalog/ExamEnglish").get("data", [])
        areas = self._get("Catalog/Area", {"isTopik": "false"}).get("data", [])
        if not isinstance(exams, list) or not isinstance(areas, list):
            raise RuntimeError("Danh mục bài thi/khu vực của IIG không đúng cấu trúc mong đợi")

        exam = self._resolve_catalog_item(
            exams, code=self.config.exam_code, name=self.config.exam_name, kind="bài thi"
        )
        area = self._resolve_catalog_item(
            areas, code=self.config.area_code, name=self.config.area_name, kind="khu vực"
        )
        if not str(exam.get("id", "")).strip() or not str(area.get("id", "")).strip():
            raise RuntimeError("Danh mục IIG tìm thấy bộ lọc nhưng thiếu UUID bài thi/khu vực")
        return exam, area

    def fetch_schedules(self) -> list[dict[str, Any]]:
        exam, area = self.resolve_filters()
        today = datetime.now(VIETNAM_TZ).date()
        end_date = today + timedelta(days=self.config.lookahead_days)
        page_size = 100
        page_index = 1
        schedules: list[dict[str, Any]] = []

        while True:
            payload = self._get(
                "ExamCalendarEnglish/GetList",
                {
                    "exam": exam["id"],
                    "area": area["id"],
                    "headerQuarterId": "",
                    "status": "true" if self.config.only_open else "",
                    "dateTest": f"{today.isoformat()},{end_date.isoformat()}",
                    "lang": "vi",
                    "pageIndex": page_index,
                    "pageSize": page_size,
                },
            )
            data = payload.get("data", [])
            if not isinstance(data, list):
                raise RuntimeError("Trường data của API lịch thi IIG không phải danh sách")
            schedules.extend(item for item in data if isinstance(item, dict))

            total_page = int(payload.get("totalPage") or 1)
            if page_index >= total_page:
                break
            page_index += 1
            if page_index > 100:
                raise RuntimeError("IIG trả quá nhiều trang; dừng để tránh vòng lặp bất thường")

        # Đối chiếu thêm bằng tên chính thức vừa lấy từ catalog. UUID trong query
        # là bộ lọc chính; bước này bảo vệ khỏi phản hồi sai/mở rộng ngoài ý muốn.
        expected_exam = normalize_text(str(exam.get("name", self.config.exam_name)))
        expected_area = normalize_text(str(area.get("name", self.config.area_name)))
        return [
            item
            for item in schedules
            if normalize_text(str(item.get("examName", ""))) == expected_exam
            and normalize_text(str(item.get("area", ""))) == expected_area
            and (not self.config.only_open or item.get("isOpen") is True)
        ]


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def schedule_fingerprint(schedule: dict[str, Any]) -> str:
    schedule_id = str(schedule.get("id", "")).strip()
    if schedule_id:
        identity: dict[str, Any] = {"id": schedule_id}
    else:
        identity = {
            key: schedule.get(key)
            for key in (
                "examName",
                "dateTest",
                "timeTest",
                "area",
                "headQuarter",
                "headQuarterAddress",
            )
        }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def latest_schedule(schedules: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(schedules)
    if not materialized:
        raise ValueError("Không có lịch thi để chọn lịch mới nhất")
    return max(
        materialized,
        key=lambda item: (
            str(item.get("dateTest", "")),
            str(item.get("timeTest", "")),
            str(item.get("id", "")),
        ),
    )


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Không đọc được state {path}: {exc}") from exc
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise RuntimeError(f"State {path} không đúng phiên bản {STATE_VERSION}")
    if not isinstance(state.get("seenFingerprints"), list):
        raise RuntimeError(f"State {path} thiếu seenFingerprints hợp lệ")
    return state


def save_state(path: Path, *, config: Config, fingerprints: list[str]) -> None:
    state = {
        "version": STATE_VERSION,
        "filter": config.filter_signature(),
        "seenFingerprints": fingerprints[-config.max_seen_fingerprints :],
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        handle.write(serialized)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def format_date(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return text or "(không rõ)"


def build_message(schedule: dict[str, Any], new_count: int) -> str:
    status = "Đang mở đăng ký" if schedule.get("isOpen") is True else "Đã đóng đăng ký"
    prefix = "🆕 <b>Có lịch thi TOEFL ITP mới</b>"
    if new_count > 1:
        prefix += f"\n<i>Phát hiện {new_count} lịch mới; dưới đây là lịch xa nhất.</i>"
    fields = [
        ("Bài thi", schedule.get("examName")),
        ("Ngày thi", format_date(schedule.get("dateTest"))),
        ("Giờ thi", schedule.get("timeTest")),
        ("Khu vực", schedule.get("area")),
        ("Địa điểm", schedule.get("headQuarter")),
        ("Địa chỉ", schedule.get("headQuarterAddress")),
        ("Trạng thái", status),
        ("Ngày trả kết quả", schedule.get("resultDate")),
    ]
    lines = [prefix, ""]
    for label, value in fields:
        if value not in (None, ""):
            lines.append(f"<b>{html.escape(label)}:</b> {html.escape(str(value))}")
    lines.extend(["", f'<a href="{html.escape(IIG_PAGE_URL)}">Xem lịch thi trên IIG Việt Nam</a>'])
    return "\n".join(lines)


def send_telegram(config: Config, message: str) -> None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("ok") is not True:
        raise RuntimeError(f"Telegram API từ chối tin nhắn: {payload}")


def run(config: Config, state_path: Path, *, dry_run: bool = False) -> int:
    schedules = IIGClient(config).fetch_schedules()
    current_by_fingerprint = {schedule_fingerprint(item): item for item in schedules}
    current_fingerprints = list(current_by_fingerprint)
    state = load_state(state_path)
    same_filter = state is not None and state.get("filter") == config.filter_signature()
    seen = list(dict.fromkeys(state.get("seenFingerprints", []))) if same_filter and state else []
    seen_set = set(seen)
    new_fingerprints = [fp for fp in current_fingerprints if fp not in seen_set]

    summary = {
        "matchedSchedules": len(schedules),
        "newSchedules": len(new_fingerprints),
        "firstRunOrFilterChanged": not same_filter,
        "statePath": str(state_path),
        "dryRun": dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if dry_run:
        if schedules:
            print("Lịch xa nhất hiện tại:")
            print(json.dumps(latest_schedule(schedules), ensure_ascii=False, indent=2))
        return 0

    if not same_filter and not config.notify_on_first_run:
        save_state(state_path, config=config, fingerprints=current_fingerprints)
        print("Đã tạo baseline lần đầu; không gửi Telegram để tránh báo lại lịch cũ.")
        return 0

    if not new_fingerprints:
        print("Không có lịch thi mới.")
        return 0

    new_schedules = [current_by_fingerprint[fp] for fp in new_fingerprints]
    newest = latest_schedule(new_schedules)
    send_telegram(config, build_message(newest, len(new_schedules)))

    combined = seen + [fp for fp in current_fingerprints if fp not in seen_set]
    save_state(state_path, config=config, fingerprints=combined)
    print(f"Đã gửi Telegram và ghi nhận {len(new_schedules)} fingerprint mới.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Theo dõi lịch TOEFL ITP trên máy tính tại Hà Nội từ IIG Việt Nam"
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(".state/iig_toefl_itp_hanoi.json"),
        help="Đường dẫn file state tối thiểu",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Đọc API và in kết quả nhưng không gửi Telegram hoặc ghi state",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # GitHub runners dùng UTF-8; cấu hình này cũng tránh lỗi cp1252/charmap trên Windows.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        args = parse_args(argv or sys.argv[1:])
        return run(Config.from_environment(), args.state, dry_run=args.dry_run)
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
