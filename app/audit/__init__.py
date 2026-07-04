"""Write to audit.audit_logs."""

from app.audit.access import audit_phi_read
from app.audit.writer import write_audit_log

__all__ = ["audit_phi_read", "write_audit_log"]
