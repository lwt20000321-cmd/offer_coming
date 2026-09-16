"""求职者创建、邮箱接续与当前摘要。"""

from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from pycore.core.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import Application, Candidate, Conversation, Session
from src.models.candidate import (
    ApplicationCreate,
    ApplicationPatchPublic,
    ApplicationPublic,
    ApplicationUpdate,
    CandidateCurrentPublic,
    CandidatePublic,
    ConversationPublic,
    LlmKeyProbeStatus,
    LlmKeyStatus,
    LlmKeyUpdatePublic,
    MessagePublic,
    NormalizedStatus,
    QuestionPublic,
    QuestionSetPublic,
    QuestionSetStatus,
    SessionRevokePublic,
    SessionStartPublic,
    TodaySummaryPublic,
)
from src.repositories.application import ApplicationRepository
from src.repositories.candidate import CandidateRepository
from src.repositories.conversation import ConversationRepository, MessageRepository
from src.repositories.question_set import QuestionSetRepository
from src.repositories.session import SessionRepository
from src.services.application_status import normalize_application_status
from src.services.llm_probe import probe_llm_api_key
from src.services.resume import (
    allowed_suffix,
    parse_resume,
    replace_resume_bytes,
    save_resume_bytes,
    validate_size,
)
from src.utils.crypto import (
    beijing_today,
    encrypt_llm_api_key,
    hash_session_token,
    new_id,
    new_session_token,
    session_expiry,
    utc_now,
)
from src.utils.email_norm import normalize_email
from src.utils.errors import (
    AppError,
    Conflict,
    Forbidden,
    InternalError,
    NotFound,
    Unauthorized,
    ValidationFailed,
)

logger = get_logger()

_JD_URL_CONFLICT = "这个投递链接已经在表里，请直接改那一行，不要再新增。"
_ONBOARDING_HINT = "请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。"
_UPDATE_KEY_HINT = "请粘贴以 sk- 开头的百炼 Key。"
_REPLACE_KEY_REQUIRED = "还没有百炼 Key，请先到「我的key」粘贴后再换简历。"
_LLM_KEY_INVALID = "这把百炼 Key 不能用，请检查后重贴。"
_LLM_API_KEY_MAX_LEN = 256
_LLM_API_KEY_PREFIX = "sk-"


class CandidateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.candidates = CandidateRepository(db)
        self.sessions = SessionRepository(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.applications = ApplicationRepository(db)
        self.question_sets = QuestionSetRepository(db)

    async def start_with_resume(
        self,
        *,
        email_raw: str | None,
        filename: str | None,
        content: bytes,
        llm_api_key: str | None,
    ) -> SessionStartPublic:
        if email_raw is None or not str(email_raw).strip() or not filename:
            raise ValidationFailed(_ONBOARDING_HINT)
        email = normalize_email(email_raw)
        suffix = allowed_suffix(filename)
        validate_size(content)
        key = self._require_llm_api_key(llm_api_key)

        existing = await self.candidates.get_by_email(email)
        if existing is not None:
            raise Conflict("这个邮箱已有记录，请直接用邮箱进入")

        probe = await probe_llm_api_key(key)
        if probe.verdict == "invalid":
            raise AppError(_LLM_KEY_INVALID, "LLM_KEY_INVALID", 400)

        candidate_id = new_id("c")
        try:
            stored = save_resume_bytes(candidate_id, filename, content)
        except OSError as exc:
            logger.error("简历写入磁盘失败", detail=str(exc))
            raise InternalError("现在存不下简历，请稍后再试。") from exc

        resume_text, parse_ok = parse_resume(stored, suffix)
        now = utc_now()
        settings = get_settings()
        candidate = Candidate(
            id=candidate_id,
            email=email,
            resume_filename=Path(filename).name,
            resume_path=str(stored),
            resume_text=resume_text,
            resume_parse_ok=parse_ok,
            llm_api_key_ciphertext=encrypt_llm_api_key(key, settings.secret_key),
            llm_key_status="saved",
            llm_key_updated_at=now,
            counseling_active=False,
            next_question_date=None,
            created_at=now,
            updated_at=now,
        )
        await self.candidates.create(candidate)
        conversation = Conversation(id=new_id("cv"), candidate_id=candidate.id, created_at=now)
        await self.conversations.create(conversation)
        token, _session = await self._issue_session(candidate.id, now)
        logger.info(
            "已创建求职者并签发会话",
            candidate_id=candidate.id,
            resume_parse_ok=parse_ok,
            llm_key_probe_status=probe.verdict,
        )
        return SessionStartPublic(
            session_token=token,
            candidate=CandidatePublic.model_validate(candidate),
            conversation_id=conversation.id,
            llm_key_probe_status=(
                "unreachable" if probe.verdict == "unreachable" else "ok"
            ),
        )

    async def continue_with_email(
        self,
        email_raw: str,
        llm_api_key: str | None = None,
    ) -> SessionStartPublic:
        email = normalize_email(email_raw)
        candidate = await self.candidates.get_by_email(email)
        if candidate is None:
            raise NotFound("这个邮箱还没有简历记录，请先上传简历并粘贴 Key。")
        submitted = self._optional_llm_api_key(llm_api_key)
        probe_status: LlmKeyProbeStatus | None = None
        if not candidate.has_llm_api_key:
            if submitted is None:
                raise AppError(
                    "这个邮箱还没有百炼 Key，请补贴后才能进入。",
                    "LLM_KEY_REQUIRED",
                    409,
                )
            probe = await probe_llm_api_key(submitted)
            if probe.verdict == "invalid":
                raise AppError(_LLM_KEY_INVALID, "LLM_KEY_INVALID", 400)
            now = utc_now()
            settings = get_settings()
            candidate.llm_api_key_ciphertext = encrypt_llm_api_key(
                submitted, settings.secret_key
            )
            candidate.llm_key_status = "saved"
            candidate.llm_key_updated_at = now
            candidate.updated_at = now
            await self.candidates.save(candidate)
            probe_status = "unreachable" if probe.verdict == "unreachable" else "ok"
            logger.info(
                "已补贴求职者 Key 并签发会话",
                candidate_id=candidate.id,
                llm_key_probe_status=probe_status,
            )
        elif submitted is not None:
            logger.info("已有 Key，忽略请求中多余的 Key", candidate_id=candidate.id)
        conversation = await self._require_conversation(candidate.id)
        token, _session = await self._issue_session(candidate.id, utc_now())
        if probe_status is None:
            logger.info("已用邮箱接续并签发新会话", candidate_id=candidate.id)
        return SessionStartPublic(
            session_token=token,
            candidate=CandidatePublic.model_validate(candidate),
            conversation_id=conversation.id,
            llm_key_probe_status=probe_status,
        )

    async def replace_resume(
        self,
        candidate: Candidate,
        *,
        filename: str | None,
        content: bytes,
    ) -> CandidateCurrentPublic:
        if not candidate.has_llm_api_key:
            raise AppError(_REPLACE_KEY_REQUIRED, "LLM_KEY_REQUIRED", 403)
        try:
            stored, resume_text = replace_resume_bytes(
                candidate.id,
                filename,
                content,
                old_path=candidate.resume_path,
            )
        except OSError as exc:
            logger.error("替换简历写入磁盘失败", detail=str(exc))
            raise InternalError("现在存不下简历，请稍后再试。") from exc
        candidate.resume_filename = Path(filename).name if filename else stored.name
        candidate.resume_path = str(stored)
        candidate.resume_text = resume_text
        candidate.resume_parse_ok = True
        candidate.updated_at = utc_now()
        await self.candidates.save(candidate)
        logger.info(
            "已替换简历",
            candidate_id=candidate.id,
            resume_filename=candidate.resume_filename,
        )
        return await self.current_summary(candidate)

    async def update_llm_key(
        self,
        candidate: Candidate,
        llm_api_key: str | None,
    ) -> LlmKeyUpdatePublic:
        key = self._require_new_llm_api_key(llm_api_key)
        probe = await probe_llm_api_key(key)
        if probe.verdict == "invalid":
            logger.info(
                "更换 Key 探测拒绝，旧密文未覆盖",
                candidate_id=candidate.id,
            )
            raise AppError(_LLM_KEY_INVALID, "LLM_KEY_INVALID", 400)
        now = utc_now()
        settings = get_settings()
        candidate.llm_api_key_ciphertext = encrypt_llm_api_key(key, settings.secret_key)
        candidate.llm_key_status = "saved"
        candidate.llm_key_updated_at = now
        candidate.updated_at = now
        await self.candidates.save(candidate)
        probe_status: LlmKeyProbeStatus = (
            "unreachable" if probe.verdict == "unreachable" else "ok"
        )
        logger.info(
            "已保存新的百炼 Key",
            candidate_id=candidate.id,
            llm_key_probe_status=probe_status,
        )
        return LlmKeyUpdatePublic(
            has_llm_api_key=True,
            llm_key_status="saved",
            llm_key_probe_status=probe_status,
        )

    async def mark_llm_key_invalid(self, candidate: Candidate) -> Candidate:
        updated = await self.candidates.mark_llm_key_invalid(candidate)
        logger.info("已标记求职者 Key 无效", candidate_id=updated.id)
        return updated

    async def revoke_current_session(self, token: str) -> SessionRevokePublic:
        settings = get_settings()
        token_hash = hash_session_token(token, settings.secret_key)
        session = await self.sessions.get_by_token_hash(token_hash)
        if session is None:
            raise Unauthorized()
        candidate_id = session.candidate_id
        await self.sessions.delete(session)
        logger.info("已作废当前接续令牌", candidate_id=candidate_id)
        return SessionRevokePublic(revoked=True)

    async def current_summary(self, candidate: Candidate) -> CandidateCurrentPublic:
        application_count = await self.applications.count_by_candidate(candidate.id)
        waiting_count = await self.applications.count_waiting_interview(candidate.id)
        today = beijing_today()
        question_set = await self.question_sets.get_for_date(candidate.id, today)
        status = question_set.status if question_set is not None else "none"
        is_rest_day = status == "rest_day"
        next_q = candidate.next_question_date
        next_q_text = next_q.isoformat() if next_q is not None else None
        is_question_day = (
            waiting_count > 0
            and (next_q_text is None or next_q_text <= today)
            and not is_rest_day
        )
        return CandidateCurrentPublic(
            id=candidate.id,
            email=candidate.email,
            resume_filename=candidate.resume_filename,
            resume_parse_ok=candidate.resume_parse_ok,
            has_llm_api_key=candidate.has_llm_api_key,
            llm_key_status=self._public_llm_key_status(candidate),
            application_count=application_count,
            waiting_interview_count=waiting_count,
            today=TodaySummaryPublic(
                beijing_date=today,
                is_question_day=is_question_day,
                is_rest_day=is_rest_day,
                question_set_status=cast(QuestionSetStatus, status),
                counseling_active=candidate.counseling_active,
            ),
        )

    async def get_or_create_conversation(self, candidate: Candidate) -> ConversationPublic:
        conversation = await self.conversations.get_by_candidate_id(candidate.id)
        if conversation is None:
            conversation = Conversation(
                id=new_id("cv"),
                candidate_id=candidate.id,
                created_at=utc_now(),
            )
            await self.conversations.create(conversation)
            logger.info("已为求职者创建唯一对话", candidate_id=candidate.id)
        return ConversationPublic.model_validate(conversation)

    async def list_messages(
        self,
        candidate: Candidate,
        conversation_id: str,
        *,
        after_id: str | None,
        limit: int | None,
    ) -> list[MessagePublic]:
        conversation = await self.conversations.get_by_id(conversation_id)
        if conversation is None:
            raise NotFound("对话不存在。")
        if conversation.candidate_id != candidate.id:
            raise Forbidden("不能查看别人的对话。")
        settings = get_settings()
        page_limit = limit or settings.message_list_default
        if page_limit > settings.message_list_max:
            page_limit = settings.message_list_max
        rows = await self.messages.list_for_conversation(
            conversation_id,
            after_id=after_id,
            limit=page_limit,
        )
        return [MessagePublic.model_validate(row) for row in rows]

    async def list_applications(self, candidate: Candidate) -> list[ApplicationPublic]:
        rows = await self.applications.list_by_candidate(candidate.id)
        return [self._to_application_public(row) for row in rows]

    async def create_application(
        self,
        candidate: Candidate,
        payload: ApplicationCreate,
    ) -> ApplicationPublic:
        jd_url = self._normalize_jd_url(payload.jd_url)
        if jd_url is not None:
            existing = await self.applications.find_by_jd_url(candidate.id, jd_url)
            if existing is not None:
                raise Conflict(_JD_URL_CONFLICT)
        applied_at = self._parse_applied_at(payload.applied_at, required=False)
        interview_at, deadline = self._parse_interview_at(payload.interview_at)
        status_text = payload.status_text or ""
        now = utc_now()
        row = Application(
            id=new_id("a"),
            candidate_id=candidate.id,
            company_name=payload.company_name or "",
            role_title=payload.role_title or "",
            jd_url=jd_url,
            jd_text="",
            exam_points="",
            progress_text=payload.progress_text or "",
            status_text=status_text,
            normalized_status=self._normalize_status(status_text),
            deadline=deadline,
            applied_at=applied_at,
            interview_at=interview_at,
            interview_summary=payload.interview_summary or "",
            created_at=now,
            updated_at=now,
        )
        try:
            row = await self.applications.create(row)
        except ValueError as exc:
            raise Conflict(_JD_URL_CONFLICT) from exc
        logger.info(
            "已新增投递行",
            application_id=row.id,
            has_jd_url=jd_url is not None,
        )
        return self._to_application_public(row)

    async def update_application(
        self,
        candidate: Candidate,
        application_id: str,
        payload: ApplicationUpdate,
    ) -> ApplicationPatchPublic:
        row = await self._owned_application(candidate, application_id, action="改")
        fields = payload.model_fields_set
        previous_status = row.normalized_status
        if "company_name" in fields:
            row.company_name = payload.company_name or ""
        if "role_title" in fields:
            row.role_title = payload.role_title or ""
        if "jd_url" in fields:
            jd_url = self._normalize_jd_url(payload.jd_url)
            if jd_url is not None:
                conflict = await self.applications.find_by_jd_url(
                    candidate.id, jd_url, exclude_id=row.id
                )
                if conflict is not None:
                    raise Conflict(_JD_URL_CONFLICT)
            row.jd_url = jd_url
        if "applied_at" in fields:
            row.applied_at = self._parse_applied_at(payload.applied_at, required=True)
        if "interview_at" in fields:
            interview_at, deadline = self._parse_interview_at(payload.interview_at)
            row.interview_at = interview_at
            row.deadline = deadline
        if "status_text" in fields:
            status_text = payload.status_text or ""
            row.status_text = status_text
            row.normalized_status = self._normalize_status(status_text)
        if "interview_summary" in fields:
            row.interview_summary = payload.interview_summary or ""
        if "progress_text" in fields:
            row.progress_text = payload.progress_text or ""
        row.updated_at = utc_now()
        try:
            row = await self.applications.save(row)
        except ValueError as exc:
            raise Conflict(_JD_URL_CONFLICT) from exc

        pending = False
        became_rejected = (
            "status_text" in fields
            and row.normalized_status == "rejected"
            and previous_status != "rejected"
        )
        if became_rejected:
            related = await self.applications.count_related_knowledge(row.id)
            if related > 0:
                from src.services.knowledge_store import KnowledgeStore

                result = await KnowledgeStore(self.db).evaluate_for_rejected(
                    candidate, row.id
                )
                pending = bool(result.get("asked"))
        logger.info(
            "已更新投递行",
            application_id=row.id,
            knowledge_review_pending=pending,
        )
        return ApplicationPatchPublic(
            **self._to_application_public(row).model_dump(),
            knowledge_review_pending=pending,
        )

    async def delete_application(self, candidate: Candidate, application_id: str) -> str:
        row = await self._owned_application(candidate, application_id, action="删")
        deleted_id = row.id
        await self.applications.delete(row)
        logger.info("已删除投递行", application_id=deleted_id)
        return deleted_id

    async def _owned_application(
        self,
        candidate: Candidate,
        application_id: str,
        *,
        action: str,
    ) -> Application:
        row = await self.applications.get_by_id(application_id)
        if row is None:
            raise NotFound("找不到这条投递。")
        if row.candidate_id != candidate.id:
            raise Forbidden(f"不能{action}别人的投递。")
        return row

    def _to_application_public(self, row: Application) -> ApplicationPublic:
        applied = row.applied_at.isoformat() if row.applied_at is not None else ""
        return ApplicationPublic(
            id=row.id,
            company_name=row.company_name,
            role_title=row.role_title,
            jd_url=row.jd_url,
            exam_points=row.exam_points,
            progress_text=row.progress_text,
            status_text=row.status_text,
            normalized_status=cast(NormalizedStatus, row.normalized_status or "other"),
            applied_at=applied,
            interview_at=row.interview_at,
            interview_summary=row.interview_summary or "",
            deadline=row.deadline,
            updated_at=row.updated_at,
        )

    def _normalize_status(self, status_text: str) -> str:
        return normalize_application_status(status_text)

    def _normalize_jd_url(self, jd_url: str | None) -> str | None:
        if jd_url is None:
            return None
        stripped = jd_url.strip()
        return stripped or None

    def _parse_applied_at(self, raw: str | None, *, required: bool) -> date:
        if raw is None or not str(raw).strip():
            if required:
                raise ValidationFailed("投递时间格式不对，请用 YYYY-MM-DD。")
            return date.fromisoformat(beijing_today())
        try:
            return date.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise ValidationFailed("投递时间格式不对，请用 YYYY-MM-DD。") from exc

    def _parse_interview_at(self, raw: str | None) -> tuple[str | None, str | None]:
        if raw is None or not str(raw).strip():
            return None, None
        value = str(raw).strip()
        tz = ZoneInfo(get_settings().timezone)
        if len(value) == 10:
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValidationFailed(
                    "面试时间格式不对，请用 YYYY-MM-DD 或带时区的时间。"
                ) from exc
            iso = parsed.isoformat()
            return iso, iso
        try:
            parsed_dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationFailed(
                "面试时间格式不对，请用 YYYY-MM-DD 或带时区的时间。"
            ) from exc
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=tz)
        local = parsed_dt.astimezone(tz)
        return local.isoformat(), local.date().isoformat()

    async def current_question_set(self, candidate: Candidate) -> QuestionSetPublic:
        today = beijing_today()
        question_set = await self.question_sets.get_for_date(candidate.id, today)
        if question_set is None:
            return QuestionSetPublic(
                id=None,
                beijing_date=today,
                status="none",
                questions=[],
                review=None,
                voided_at=None,
            )
        questions = await self.question_sets.list_questions(question_set.id)
        return QuestionSetPublic(
            id=question_set.id,
            beijing_date=question_set.beijing_date,
            status=cast(QuestionSetStatus, question_set.status),
            questions=[QuestionPublic.model_validate(item) for item in questions],
            review=question_set.review,
            voided_at=question_set.voided_at,
        )

    async def candidate_by_token(self, token: str) -> Candidate:
        settings = get_settings()
        token_hash = hash_session_token(token, settings.secret_key)
        session = await self.sessions.get_by_token_hash(token_hash)
        if session is None:
            raise Unauthorized()
        candidate = await self.candidates.get_by_id(session.candidate_id)
        if candidate is None:
            raise Unauthorized()
        return candidate

    async def _issue_session(self, candidate_id: str, now) -> tuple[str, Session]:
        settings = get_settings()
        token = new_session_token()
        session = Session(
            id=new_id("s"),
            candidate_id=candidate_id,
            token_hash=hash_session_token(token, settings.secret_key),
            expires_at=session_expiry(settings.session_ttl_days, now),
            created_at=now,
        )
        await self.sessions.create(session)
        return token, session

    async def _require_conversation(self, candidate_id: str) -> Conversation:
        conversation = await self.conversations.get_by_candidate_id(candidate_id)
        if conversation is None:
            conversation = Conversation(
                id=new_id("cv"),
                candidate_id=candidate_id,
                created_at=utc_now(),
            )
            await self.conversations.create(conversation)
        return conversation

    def _require_llm_api_key(self, raw: str | None) -> str:
        key = self._optional_llm_api_key(raw)
        if key is None:
            raise ValidationFailed(_ONBOARDING_HINT)
        return key

    def _require_new_llm_api_key(self, raw: str | None) -> str:
        key = self._parse_llm_api_key(raw, invalid_message=_UPDATE_KEY_HINT)
        if key is None:
            raise ValidationFailed(_UPDATE_KEY_HINT)
        return key

    def _optional_llm_api_key(self, raw: str | None) -> str | None:
        return self._parse_llm_api_key(raw, invalid_message=_ONBOARDING_HINT)

    def _parse_llm_api_key(self, raw: str | None, *, invalid_message: str) -> str | None:
        if raw is None:
            return None
        key = str(raw).strip()
        if not key:
            return None
        if not key.startswith(_LLM_API_KEY_PREFIX) or len(key) > _LLM_API_KEY_MAX_LEN:
            raise ValidationFailed(invalid_message)
        return key

    def _public_llm_key_status(self, candidate: Candidate) -> LlmKeyStatus:
        if not candidate.has_llm_api_key:
            return "missing"
        if candidate.llm_key_status == "invalid":
            return "invalid"
        return "saved"
