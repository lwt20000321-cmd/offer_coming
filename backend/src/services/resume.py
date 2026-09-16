"""简历原件保存与文本抽取。解析失败仍保留原件。"""

from pathlib import Path

from pycore.core.logger import get_logger

from src.config.settings import get_settings
from src.utils.errors import ValidationFailed
from src.utils.paths import resolve_upload_dir

logger = get_logger()

_ONBOARDING_RESUME_HINT = "请先填写邮箱并上传简历，才能和小凹对话。"
_REPLACE_RESUME_HINT = "请上传 pdf、docx 或 txt 简历文件。"
_REPLACE_PARSE_FAIL = "这份简历读不出来，原来的简历还在，请换一份再试。"


def allowed_suffix(filename: str | None, *, missing_hint: str | None = None) -> str:
    settings = get_settings()
    if not filename:
        raise ValidationFailed(missing_hint or _ONBOARDING_RESUME_HINT)
    suffix = Path(filename).suffix.lower()
    allowed = {
        item.lower() if item.startswith(".") else f".{item.lower()}"
        for item in settings.resume_allowed_extensions
    }
    if suffix not in allowed:
        raise ValidationFailed("简历格式不支持，请上传 pdf、docx 或 txt 文件。")
    return suffix


def validate_size(content: bytes, *, empty_hint: str | None = None) -> None:
    settings = get_settings()
    if not content:
        raise ValidationFailed(empty_hint or _ONBOARDING_RESUME_HINT)
    if len(content) > settings.resume_max_bytes:
        raise ValidationFailed(
            f"简历文件过大，请上传不超过 {settings.resume_max_bytes} 字节的 pdf、docx 或 txt。"
        )


def save_resume_bytes(candidate_id: str, filename: str, content: bytes) -> Path:
    settings = get_settings()
    upload_root = resolve_upload_dir(settings.upload_dir)
    safe_name = Path(filename).name.replace("/", "_").replace("\\", "_")
    dest = upload_root / candidate_id / safe_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.info("简历原件已保存", candidate_id=candidate_id)
    return dest


def _safe_resume_name(filename: str) -> str:
    return Path(filename).name.replace("/", "_").replace("\\", "_")


def _cleanup_staging(staging: Path, staging_dir: Path) -> None:
    try:
        if staging.exists():
            staging.unlink()
    except OSError as exc:
        logger.warning("替换简历的临时文件未能删除", detail=str(exc))
    try:
        if staging_dir.exists() and not any(staging_dir.iterdir()):
            staging_dir.rmdir()
    except OSError:
        return


def replace_resume_bytes(
    candidate_id: str,
    filename: str | None,
    content: bytes,
    *,
    old_path: str,
) -> tuple[Path, str]:
    """先解析 staging；成功后才覆盖目标文件。失败时旧档不变。"""
    suffix = allowed_suffix(filename, missing_hint=_REPLACE_RESUME_HINT)
    validate_size(content, empty_hint=_REPLACE_RESUME_HINT)
    settings = get_settings()
    upload_root = resolve_upload_dir(settings.upload_dir)
    candidate_dir = upload_root / candidate_id
    staging_dir = candidate_dir / ".staging"
    safe_name = _safe_resume_name(filename or "resume")
    staging = staging_dir / safe_name
    dest = candidate_dir / safe_name
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(content)
    except OSError:
        _cleanup_staging(staging, staging_dir)
        raise
    try:
        resume_text, parse_ok = parse_resume(staging, suffix)
        if not parse_ok:
            raise ValidationFailed(_REPLACE_PARSE_FAIL)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(dest)
    except (ValidationFailed, OSError):
        _cleanup_staging(staging, staging_dir)
        raise
    _cleanup_staging(staging, staging_dir)
    old = Path(old_path)
    try:
        if old.exists() and old.resolve() != dest.resolve():
            old.unlink()
    except OSError as exc:
        logger.warning("旧简历原件未能删除", detail=str(exc))
    logger.info("简历原件已替换", candidate_id=candidate_id)
    return dest, resume_text


def parse_resume(path: Path, suffix: str) -> tuple[str, bool]:
    try:
        if suffix == ".txt":
            text = _decode_text(path.read_bytes())
        elif suffix == ".pdf":
            text = _parse_pdf(path)
        elif suffix == ".docx":
            text = _parse_docx(path)
        else:
            return "", False
        text = text.strip()
        if not text:
            logger.warning("简历解析结果为空，仍保存原件")
            return "", False
        return text, True
    except Exception as exc:
        logger.warning("简历解析失败，仍保存原件", detail=str(exc))
        return "", False


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _parse_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)
