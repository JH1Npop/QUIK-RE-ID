from . import roles


def nav(request):
    """Role flags for the top bar."""
    names = roles.role_names(request.user) if hasattr(request, "user") else set()
    return {
        "is_student": roles.STUDENT in names,
        "is_program_head": roles.PROGRAM_HEAD in names,
        "is_osas": roles.OSAS in names,
        "is_id_shop": roles.ID_SHOP in names,
        "is_admin_role": roles.ADMIN in names,
        "user_role": roles.primary_role(request.user) if hasattr(request, "user") else None,
    }
