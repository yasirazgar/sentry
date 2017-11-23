from __future__ import absolute_import
from sentry.models import (
    ApiKey, AuditLogEntry, AuditLogEntryEvent, DeletedOrganization, DeletedProject, Project, Team, User
)


def create_audit_entry(request, transaction_id=None, logger=None, **kwargs):

    def complete_delete_entry(delete_log, entry):
        delete_log.actor_label = entry.actor_label
        delete_log.actor_id = entry.actor_id
        delete_log.actor_key = entry.actor_key
        delete_log.ip_address = entry.ip_address

    user = request.user if request.user.is_authenticated() else None
    api_key = request.auth if hasattr(request, 'auth') \
        and isinstance(request.auth, ApiKey) else None

    entry = AuditLogEntry(
        actor=user, actor_key=api_key, ip_address=request.META['REMOTE_ADDR'], **kwargs
    )

    # Only create a real AuditLogEntry record if we are passing an event type
    # otherwise, we want to still log to our actual logging
    if entry.event is not None:
        entry.save()

    if entry.event == AuditLogEntryEvent.ORG_REMOVE:
        delete_log = DeletedOrganization()
        delete_log.name = entry.organization.name
        delete_log.slug = entry.organization.slug
        delete_log.date_created = entry.organization.date_added

        complete_delete_entry(delete_log, entry)

        delete_log.save()

    elif entry.event == AuditLogEntryEvent.PROJECT_REMOVE:
        delete_log = DeletedProject()

        project = Project.objects.get(id=entry.target_object)
        delete_log.name = project.name
        delete_log.slug = project.slug
        delete_log.date_created = project.date_added

        delete_log.organization_id = entry.organization.id
        delete_log.organization_name = entry.organization.name
        delete_log.organization_slug = entry.organization.slug

        team = project.team
        delete_log.team_id = team.id
        delete_log.team_name = team.name
        delete_log.team_slug = team.slug

        complete_delete_entry(delete_log, entry)

        delete_log.save()
    elif entry.event == AuditLogEntryEvent.TEAM_REMOVE:
        delete_log = DeletedProject()

        team = Team.objects.get(id=entry.target_object)
        delete_log.name = team.name
        delete_log.slug = team.slug
        delete_log.date_created = team.date_added

        delete_log.organization_id = entry.organization.id
        delete_log.organization_name = entry.organization.name
        delete_log.organization_slug = entry.organization.slug

        complete_delete_entry(delete_log, entry)
        delete_log.save()
    elif entry.event == AuditLogEntryEvent.MEMBER_REMOVE:
        delete_log = DeletedProject()

        user = User.objects.get(id=entry.target_object)
        delete_log.username = user.username
        delete_log.email = user.email
        delete_log.is_staff = user.is_staff
        delete_log.is_superuser = user.is_superuser
        delete_log.date_created = user.date_joined

        complete_delete_entry(delete_log, entry)
        delete_log.save()
    extra = {
        'ip_address': entry.ip_address,
        'organization_id': entry.organization_id,
        'object_id': entry.target_object,
        'entry_id': entry.id,
        'actor_label': entry.actor_label
    }
    if entry.actor_id:
        extra['actor_id'] = entry.actor_id
    if entry.actor_key_id:
        extra['actor_key_id'] = entry.actor_key_id
    if transaction_id is not None:
        extra['transaction_id'] = transaction_id

    if logger:
        logger.info(entry.get_event_display(), extra=extra)

    return entry