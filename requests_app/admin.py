from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import ApprovalLog, Department, IdShop, Payment, ReplacementRequest, Upload, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "email", "department", "role_list", "is_active")
    list_filter = ("groups", "department", "is_active", "is_staff")
    search_fields = ("username", "full_name", "email", "lrn")
    fieldsets = BaseUserAdmin.fieldsets + (("Portal", {"fields": ("honorific", "full_name", "title", "lrn", "department", "shop")}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Portal", {"fields": ("full_name", "email", "lrn", "department", "groups")}),
    )

    @admin.display(description="Roles")
    def role_list(self, obj):
        return ", ".join(g.name for g in obj.groups.all())

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "program_head")
    search_fields = ("name",)
    autocomplete_fields = ("program_head",)


def _file_link(upload):
    # Name only: uploads are private and shown only to the owner and the staff handling the request.
    return upload.file.name.rsplit("/", 1)[-1]


class UploadInline(admin.TabularInline):
    model = Upload
    fields = ("kind", "file_link", "uploaded_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    @admin.display(description="File")
    def file_link(self, obj):
        return _file_link(obj)

    def has_add_permission(self, request, obj=None):
        return False


class ApprovalLogInline(admin.TabularInline):
    model = ApprovalLog
    fields = ("created_at", "action", "actor", "remarks")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.StackedInline):
    model = Payment
    fields = ("amount", "receipt_no", "recorded_by", "paid_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(IdShop)
class IdShopAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(ReplacementRequest)
class ReplacementRequestAdmin(admin.ModelAdmin):
    list_display = ("reference_no", "student", "status", "created_at", "updated_at")
    list_filter = ("status", "student__department")
    search_fields = ("reference_no", "student__username", "student__full_name")
    # Status only changes through the portal, where the rules and the log are enforced.
    readonly_fields = ("reference_no", "student", "status", "shop", "created_at", "updated_at")
    fields = ("reference_no", "student", "status", "shop", "reason", "date_lost", "place_lost", "remarks",
              "created_at", "updated_at")
    inlines = [UploadInline, PaymentInline, ApprovalLogInline]

    def has_add_permission(self, request):
        return False


@admin.register(Upload)
class UploadAdmin(admin.ModelAdmin):
    list_display = ("request", "kind", "file_link", "uploaded_at")
    list_filter = ("kind",)
    search_fields = ("request__reference_no",)
    fields = ("request", "kind", "file_link", "uploaded_at")
    readonly_fields = fields

    @admin.display(description="File")
    def file_link(self, obj):
        return _file_link(obj)

    def has_add_permission(self, request):
        return False


@admin.register(ApprovalLog)
class ApprovalLogAdmin(admin.ModelAdmin):
    list_display = ("request", "action", "actor", "created_at")
    list_filter = ("action",)
    search_fields = ("request__reference_no", "actor__username")
    readonly_fields = ("request", "action", "actor", "remarks", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("receipt_no", "request", "amount", "recorded_by", "paid_at")
    search_fields = ("receipt_no", "request__reference_no")
    readonly_fields = ("request", "recorded_by")

    def has_add_permission(self, request):
        return False
