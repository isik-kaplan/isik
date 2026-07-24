from dalf.admin import DALFModelAdmin, DALFRelatedFieldAjax
from django.contrib.admin import ModelAdmin
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from isik.common.utils.concurrency import ThreadLock
from isik.common.utils.functional import with_attrs
from isik.django.apps.common.db import BaseModel


class BaseAdmin(DjangoObjectActions, DALFModelAdmin):
    """
    Base admin class for all models. Provides a set of conventions for
    readonly fields, list display, fieldsets, and autocomplete filters.

    Attributes:
        search_fields: Fields to search by in the admin list view.
        update_readonly_fields: Fields that are readonly when updating an existing object.
        create_readonly_fields: Fields that are readonly when creating a new object.
        global_readonly_fields: Fields that are always readonly regardless of create or update.
            BaseModel.FIELDS (id, slug, created_at, updated_at) are always appended to readonly fields.
        list_filter: Standard Django list filters, passed through directly.
        autocomplete_list_filter: Field names to filter by using DALF AJAX autocomplete.
            These are combined with list_filter in get_list_filter.
        list_display: Fields to display in the admin list view.
            BaseModel.FIELDS are always appended, except those in excluded_list_display.
        excluded_list_display: Fields from BaseModel.FIELDS to exclude from list_display.
            Defaults to ["slug"].
        object_fieldsets: Fieldsets for the main object fields, shown on both create and update.
            Format: [[[field1, field2, ...], _("Section Name")], ...]
        meta_fieldsets: Fieldsets for meta/informational fields, shown on both create and update.
            Format: [[[field1, field2, ...], _("Section Name")], ...]
            Defaults to a single "Meta" fieldset containing BaseModel.FIELDS.
        safe_m2m_fields: M2M fields that use a through table not marked as auto_created.
            Normally Django hides these from the admin form — listing them here temporarily
            marks them as auto_created during form rendering so they appear correctly.
        create_force_field_as_current_user: Fields to force-set to request.user when creating.
        update_force_field_as_current_user: Fields to force-set to request.user when updating.
        global_force_field_as_current_user: Fields to always force-set to request.user
            regardless of create or update.
    """

    search_fields = []
    update_readonly_fields = []
    create_readonly_fields = []
    global_readonly_fields = []
    list_filter = []
    autocomplete_list_filter = []
    list_display = []
    object_fieldsets = []
    meta_fieldsets = [[BaseModel.FIELDS, _("Meta")]]
    excluded_list_display = ["slug"]
    safe_m2m_fields = []
    create_force_field_as_current_user = []
    update_force_field_as_current_user = []
    global_force_field_as_current_user = []

    @staticmethod
    def make_fieldset_field(*fields, name):
        return [name, {"fields": fields}]

    def get_list_filter(self, request):
        autocomplete_filters = [(f, DALFRelatedFieldAjax) for f in self.autocomplete_list_filter]
        return autocomplete_filters + self.list_filter

    def get_search_fields(self, request):
        return self.search_fields

    def get_readonly_fields(self, request, obj=None):
        base = BaseModel.FIELDS
        if obj:
            return self.update_readonly_fields + self.global_readonly_fields + base
        return self.create_readonly_fields + self.global_readonly_fields + base

    def get_list_display(self, request):
        base = [key for key in BaseModel.FIELDS if key not in self.excluded_list_display]
        return self.list_display + base

    def get_fieldsets(self, request, obj=None):
        object_fieldsets = [self.make_fieldset_field(*fields, name=name) for fields, name in self.object_fieldsets]
        meta_fieldsets = [self.make_fieldset_field(*fields, name=name) for fields, name in self.meta_fieldsets]
        return object_fieldsets + meta_fieldsets

    def save_model(self, request, obj, form, change):
        fields = self.update_force_field_as_current_user if change else self.create_force_field_as_current_user
        fields = fields + self.global_force_field_as_current_user
        for field in fields:
            setattr(obj, field, request.user)
        return super().save_model(request, obj, form, change)

    @with_attrs(thread_lock=ThreadLock("BaseAdmin.formfield_for_manytomany"))
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """
        This allows creation of many-to-many fields that have custom through models that do not require extra field
        input. For example this can be a Tag model with an extra created_at field.
        By default you can't add these as m2m field because django thinks you need to fill in the extra field.
        If your m2m field satisfies this result, add it to the safe_m2m_fields.
        """
        if db_field.name in self.safe_m2m_fields:
            with self.formfield_for_manytomany.thread_lock:
                db_field.remote_field.through._meta.auto_created = True
                form_field = ModelAdmin.formfield_for_manytomany(self, db_field, request, **kwargs)
                db_field.remote_field.through._meta.auto_created = False
        else:
            form_field = ModelAdmin.formfield_for_manytomany(self, db_field, request, **kwargs)
        return form_field
