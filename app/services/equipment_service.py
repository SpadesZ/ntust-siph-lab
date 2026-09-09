# ============================================================
# NTUST SiPh Lab - Equipment Service
#
# 上下游：
#   blueprints/admin/routes.py（CRUD）
#     -> services/equipment_service.py（本檔）
#     -> models/equipment.py + AuditLog
#     -> repositories/equipment.py 供公開頁讀取
#
# 檔案路徑：
#   app/services/equipment_service.py
#
# 建立日期：2026-09-09 / 版本：v1.0
#
# 模組定位與責任邊界：
#   設備資料的寫入路徑：建立、更新、發布、下架、刪除。
#   所有變更都寫 AuditLog（SAI §8.8）。
#
#   責任邊界（不得做的事）：
#     - 不得在此查詢公開資料（那是 repository）。
#     - 不得繞過 PublishValidator 直接把項目設為 published。
#
# 為什麼發布要過 validator：
#   這張表的資料多數來自公開報導整理，而非實驗室自述。缺出處的
#   項目一旦發布，讀者就看到一台無法查證的機台 —— publish() 因此
#   強制檢查，而不是把驗證留給後台介面的善意提醒。
#
# 主要 Class / Function：
#   EquipmentServiceError - 業務例外
#   EquipmentService.create / update / publish / unpublish / delete
#
# 特殊機制：
#   slug 沿用 ResearchService 的規則：一旦產生就不隨名稱變更而改動
#   （SAI §4.2）。但設備沒有詳細頁，slug 只用於頁面內錨點，因此
#   變更時「不」建立 301 redirect —— 錨點失效不是 404，建立一條
#   指向同一個頁面的 redirect 只會讓 redirect 表長出無用的資料。
#
# 驗證方式：
#   pytest tests/test_equipment.py
# ============================================================

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.equipment import Equipment
from app.models.mixins import (
    AuditAction,
    EquipmentCategory,
    EquipmentOwnership,
    PublishStatus,
)
from app.services.publish_validator import PublishValidator
from app.utils.slugs import ensure_unique_slug, slugify

logger = logging.getLogger(__name__)

_SLUG_MAX = 160

#: 可由表單直接指派的欄位。publish_status 不在其中 —— 發布必須
#: 走 publish()，才會經過門檻檢查。
_ASSIGNABLE = (
    "name_zh", "name_en", "manufacturer", "model_number",
    "description_zh", "description_en", "specs_zh", "specs_en",
    "location_zh", "location_en", "source_note", "source_url",
    "photo_alt_zh", "photo_alt_en",
)


class EquipmentServiceError(RuntimeError):
    """設備寫入的業務例外。"""


class EquipmentService:
    """設備資料的寫入路徑。"""

    @staticmethod
    def _commit(action_summary: str) -> None:
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            logger.warning("設備寫入違反約束（%s）：%s", action_summary, exc)
            raise EquipmentServiceError("儲存失敗：slug 可能已被使用。") from exc
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.exception("設備寫入失敗（%s）", action_summary)
            raise EquipmentServiceError(f"儲存失敗：{exc}") from exc

    @staticmethod
    def _apply_slug(item: Equipment, desired: str | None) -> None:
        """設定 slug。已存在且未指定新值時保持不變。"""
        base = desired.strip() if desired and desired.strip() else None

        if not base:
            if item.slug:
                return
            source = item.name_en or item.name_zh or "equipment"
            base = slugify(source, max_length=_SLUG_MAX, fallback_prefix="equipment")
        else:
            base = slugify(base, max_length=_SLUG_MAX, fallback_prefix="equipment")

        item.slug = ensure_unique_slug(
            Equipment, base, exclude_id=item.id, max_length=_SLUG_MAX
        )

    @staticmethod
    def _assign_fields(item: Equipment, data: dict) -> None:
        for field in _ASSIGNABLE:
            if field in data:
                value = data[field]
                setattr(item, field, value.strip() if isinstance(value, str) else value)

        ownership = data.get("ownership")
        if ownership in EquipmentOwnership.ALL:
            item.ownership = ownership

        category = data.get("category")
        if category in EquipmentCategory.ALL:
            item.category = category

        if "is_featured" in data:
            item.is_featured = bool(data["is_featured"])

        sort_order = data.get("sort_order")
        if sort_order is not None:
            item.sort_order = int(sort_order)

    @staticmethod
    def create(
        data: dict, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Equipment:
        """建立設備（一律為草稿）。"""
        if not ((data.get("name_zh") or "").strip() or (data.get("name_en") or "").strip()):
            raise EquipmentServiceError("設備名稱至少需填寫一個語言版本。")

        item = Equipment(slug="", publish_status=PublishStatus.DRAFT)
        EquipmentService._assign_fields(item, data)

        db.session.add(item)
        db.session.flush()
        EquipmentService._apply_slug(item, data.get("slug"))

        AuditLog.write(
            action=AuditAction.CREATE,
            entity_type="equipment",
            entity_id=item.id,
            summary=f"建立設備 {item.name_zh or item.name_en}（{item.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        EquipmentService._commit(f"create equipment {item.slug}")
        return item

    @staticmethod
    def update(
        item: Equipment,
        data: dict,
        admin_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> Equipment:
        """更新設備欄位（不改變發布狀態）。"""
        EquipmentService._assign_fields(item, data)
        EquipmentService._apply_slug(item, data.get("slug"))

        AuditLog.write(
            action=AuditAction.UPDATE,
            entity_type="equipment",
            entity_id=item.id,
            summary=f"更新設備 {item.name_zh or item.name_en}（{item.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        EquipmentService._commit(f"update equipment {item.slug}")
        return item

    @staticmethod
    def publish(
        item: Equipment, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Equipment:
        """發布設備；未通過門檻時拒絕並回報原因。"""
        result = PublishValidator.validate_equipment(item)
        if not result.is_valid:
            raise EquipmentServiceError(
                "無法發布：" + "；".join(result.error_messages())
            )

        item.publish_status = PublishStatus.PUBLISHED
        AuditLog.write(
            action=AuditAction.PUBLISH,
            entity_type="equipment",
            entity_id=item.id,
            summary=f"發布設備 {item.name_zh or item.name_en}（{item.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        EquipmentService._commit(f"publish equipment {item.slug}")
        return item

    @staticmethod
    def unpublish(
        item: Equipment, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> Equipment:
        """退回草稿。

        同時取消 is_featured：精選是「在已發布項目中特別突出」的
        意思，草稿不可能被突出，留著它只會在下次發布時悄悄生效。
        """
        item.publish_status = PublishStatus.DRAFT
        item.is_featured = False

        AuditLog.write(
            action=AuditAction.ARCHIVE,
            entity_type="equipment",
            entity_id=item.id,
            summary=f"設備退回草稿 {item.name_zh or item.name_en}（{item.slug}）",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        EquipmentService._commit(f"unpublish equipment {item.slug}")
        return item

    @staticmethod
    def delete(
        item: Equipment, admin_user_id: int | None = None, ip_address: str | None = None
    ) -> None:
        """刪除設備。"""
        label = f"{item.name_zh or item.name_en}（{item.slug}）"
        entity_id = item.id

        db.session.delete(item)
        AuditLog.write(
            action=AuditAction.DELETE,
            entity_type="equipment",
            entity_id=entity_id,
            summary=f"刪除設備 {label}",
            admin_user_id=admin_user_id,
            ip_address=ip_address,
        )
        EquipmentService._commit(f"delete equipment {entity_id}")
