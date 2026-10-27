"""tags() maker + TaggableMixin - see `tags` for usage."""

from django.core.exceptions import ImproperlyConfigured
from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy, lazy_format
from isik.django.apps.common._contracts import Contract, foreign_key, kind, the_foreign_key_to
from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose,
    keyword_list,
    merge_fields,
    resolve_base_model,
    resolve_field,
    when_registered,
)


TAG_NAME_REGEX = r"^[a-zA-Z0-9_.-]+$"
TAG_NAME_REGEX_ERROR_MESSAGE = gettext_lazy("Tags can only contain letters, numbers, ., - and _.")


def _default_name_validators():
    return [RegexValidator(TAG_NAME_REGEX, TAG_NAME_REGEX_ERROR_MESSAGE)]


class TagQuerySet(models.QuerySet):
    def to_list(self):
        """Plain list of tag name strings - e.g. what `generic_tag_field()` reads back."""
        return list(self.values_list("name", flat=True))

    def normalized(self, name):
        """`name` as this pool keeps it - through the maker's `normalize=`, if it has one."""
        normalize = getattr(self.model, "_normalize", None)
        return normalize(name) if normalize else name

    def get_tag(self, name):
        """Get-or-create by name, applying the maker's `normalize=` first if configured, then
        cleaning it through the `name` field (its validators, e.g. `name_validators`, and its blank
        check, which the validators skip) - `create()` bypasses `full_clean()`, so without this an
        invalid name would otherwise slip through untouched."""
        name = self.normalized(name)
        self.model._meta.get_field("name").clean(name, None)
        return self.create(name=name)

    def create(self, **kwargs):
        # filter-then-create, not Django's own get_or_create() - so a second request that also
        # passed the filter check before either insert lands still has to be reconciled against
        # the `name` unique constraint, not left to crash the request with a raw IntegrityError.
        if tag := self.filter(name=kwargs["name"]).first():
            return tag
        try:
            with transaction.atomic():
                return super().create(**kwargs)
        except IntegrityError:
            return self.get(name=kwargs["name"])


class TagManager(models.Manager.from_queryset(TagQuerySet)):
    def from_list(self, names):
        """Get-or-creates every name in `names`, returns the resulting queryset."""
        tag_ids = [self.get_tag(name).id for name in names]
        return self.filter(pk__in=tag_ids)


class AbstractTag(models.Model):
    """
    A base for a tag model a project writes itself - `tags(tag_model=...)` - with what the pool reads:
    a unique `name`, validated as `tags()` validates its own, and `TagManager` as `objects`.
    Override `name` for another length or other validators; give the model `_normalize` - a function
    of a name - to normalize it as `tags(normalize=...)` does.
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        validators=_default_name_validators(),
        help_text=gettext_lazy("The tag's name, unique in its pool."),
        db_comment="The tag's name, unique in its pool.",
    )

    objects = TagManager()

    class Meta:
        abstract = True


def _tag_contract():
    return Contract(
        maker="tags()",
        needs=(kind("name", models.CharField, _("a unique CharField")),),
        unique=(("name",),),
    )


def _check_tag_model(model):
    _tag_contract().check(model)
    if not isinstance(model._default_manager, TagManager):
        raise ImproperlyConfigured(
            _("%(model)s can't be tags()'s model: its default manager must be a TagManager - see AbstractTag.")
            % {"model": model._meta.label}
        )


class _TagsField:
    """Descriptor returned by `tags()` - see `tags` for the public docstring."""

    def __init__(
        self,
        *,
        related_name,
        target_name,
        target_related_name,
        name_max_length,
        name_validators,
        normalize,
        tag_base_model,
        through_base_model,
        tag_extra_fields,
        through_extra_fields,
        tag_model_name,
        through_model_name,
        tag_model,
        through_model,
    ):
        tag_options = {
            "name_max_length": name_max_length,
            "name_validators": name_validators,
            "normalize": normalize,
            "tag_base_model": tag_base_model,
            "tag_extra_fields": tag_extra_fields,
            "tag_model_name": tag_model_name,
        }
        through_options = {
            "target_related_name": target_related_name,
            "through_base_model": through_base_model,
            "through_extra_fields": through_extra_fields,
            "through_model_name": through_model_name,
        }
        if through_model is not None and tag_model is None:
            raise TypeError(_("tags(through_model=...) needs tag_model= too - its tag key points at that model"))
        for given, options, model_option in (
            (tag_model, tag_options, "tag_model"),
            (through_model, through_options, "through_model"),
        ):
            refused = sorted(option for option, value in options.items() if value is not None)
            if given is not None and refused:
                raise TypeError(
                    _("tags(%(option)s=...) uses the model as it is - drop %(options)s")
                    % {"option": model_option, "options": keyword_list(refused)}
                )
        self.related_name = related_name
        self.target_name = target_name
        self.target_related_name = target_related_name or "tags"
        self.name_max_length = name_max_length or 100
        self.name_validators = name_validators if name_validators is not None else _default_name_validators()
        self.normalize = normalize
        self.tag_base_model = tag_base_model
        self.through_base_model = through_base_model
        self.tag_extra_fields = tag_extra_fields or {}
        self.through_extra_fields = through_extra_fields or {}
        self.tag_model_name = tag_model_name
        self.through_model_name = through_model_name
        self.tag_model = tag_model
        self.through_model = through_model

    def contribute_to_class(self, host_cls, name):
        self.host = host_cls
        self.attname = name
        through_model_name = self.through_model_name or f"{host_cls.__name__}{name.capitalize()}ObjectTag"
        if self.tag_model is None:
            tag_model = self._build_tag_model(host_cls, name)
        else:
            tag_model = self.tag_model
            when_registered(host_cls, tag_model, self._bind_tag_model)
        if self.through_model is None:
            claim_related_name(host_cls, self.target_related_name, through_model_name)
            self.target_name = self.target_name or "target"
            through_model = self._build_through_model(host_cls, tag_model, through_model_name)
        else:
            through_model = self.through_model
            when_registered(host_cls, through_model, self._bind_through_model)

        # No db_comment: a many-to-many has no column of its own - its through model's columns say it.
        m2m_field = models.ManyToManyField(
            tag_model,
            through=through_model,
            related_name=self.related_name,
            blank=True,
            help_text=gettext_lazy("Tags from this pool."),
        )
        m2m_field.contribute_to_class(host_cls, name)
        descriptor = getattr(host_cls, name)
        expose(host_cls, name, descriptor, generated_model=tag_model, config=self)

    def _build_tag_model(self, host_cls, name):
        model_name = self.tag_model_name or f"{host_cls.__name__}{name.capitalize()}Tag"
        tag_fields = {
            "name": models.CharField(
                max_length=self.name_max_length,
                unique=True,
                validators=self.name_validators,
                help_text=gettext_lazy("The tag's name, unique in its pool."),
                db_comment="The tag's name, unique in its pool.",
            ),
            "objects": TagManager(),
        }
        return build_model(
            model_name,
            host_cls,
            fields=merge_fields(tag_fields, self.tag_extra_fields, model_name),
            base_model=resolve_base_model(self.tag_base_model, "TAGS_TAG_BASE_MODEL"),
            extra_attrs={"_normalize": self.normalize},
        )

    def _build_through_model(self, host_cls, tag_model, model_name):
        through_fields = {
            "tag": models.ForeignKey(
                tag_model,
                on_delete=models.CASCADE,
                help_text=gettext_lazy("The tag applied."),
                db_comment="The tag applied.",
            ),
            self.target_name: models.ForeignKey(
                host_cls,
                on_delete=models.CASCADE,
                related_name=self.target_related_name,
                help_text=lazy_format("The %(target)s tagged.", target=host_cls.__name__),
                db_comment=f"The {host_cls.__name__} tagged.",
            ),
        }
        return build_model(
            model_name,
            host_cls,
            fields=merge_fields(through_fields, self.through_extra_fields, model_name),
            base_model=resolve_base_model(self.through_base_model, "TAGS_THROUGH_BASE_MODEL"),
            meta_attrs={
                "constraints": [
                    models.UniqueConstraint(fields=["tag", self.target_name], name=f"unique_{model_name.lower()}")
                ]
            },
        )

    def _bind_tag_model(self, model):
        _check_tag_model(model)
        getattr(self.host, self.attname).model = model

    def _bind_through_model(self, model):
        key = the_foreign_key_to(model, self.host, self.target_name, "tags()")
        self.target_name = key.name
        Contract(
            maker="tags()",
            needs=(foreign_key("tag", self.tag_model, _("a ForeignKey to the tag model")),),
            unique=(("tag", key.name),),
        ).check(model)


def tags(
    *,
    related_name,
    target_name=None,
    target_related_name=None,
    name_max_length=None,
    name_validators=None,
    normalize=None,
    tag_base_model=None,
    through_base_model=None,
    tag_extra_fields=None,
    through_extra_fields=None,
    tag_model_name=None,
    through_model_name=None,
    tag_model=None,
    through_model=None,
):
    """
    Attaches a per-host-model tag pool plus a M2M through-table, and mixes in the ability to
    manage it via `TaggableMixin`.

        class Post(TaggableMixin, models.Model):
            topics = tags(related_name="posts_with_topic")

        post.add_tag("python"); post.set_tags(["python", "django"]); post.tag_names()
        post.topics.all()      # real Django M2M manager
        post.topics.through     # native to any M2M descriptor - the generated through model
        Post.topics.model       # the generated PostTopicsTag model

    Tags are deduped by `name` within one attachment's pool - `Post.topics` and `Post.labels` (a
    second `tags()` on the same host) get entirely separate pools, never sharing a `Tag` row.
    `related_name` is required (Tag -> host reverse accessor, `tag.<related_name>`); `target_name`/
    `target_related_name` are the through model's FK-to-host knobs, same as the feedback makers -
    `target_related_name` is claimed via `claim_related_name`, so two tag dimensions on the same
    host must give at least one of them a non-default `target_related_name`.

    `name_max_length`/`name_validators` control the generated `name` field (default: one or more
    letters, digits, `.`, `-` or `_`, via `RegexValidator`). `normalize` is an optional callable (e.g. `str.lower`)
    applied to a name before every dedup lookup/create - off by default, so `"Python"` and
    `"python"` are different tags unless you opt in.

    `tag_base_model`/`through_base_model` (or the `TAGS_TAG_BASE_MODEL`/`TAGS_THROUGH_BASE_MODEL`
    settings) let the two generated models inherit your own base. `tag_extra_fields`/
    `through_extra_fields` merge in extra fields (or other makers) onto the Tag model and the
    through model respectively.

    A host can attach `tags()` more than once - `TaggableMixin` methods auto-pick the sole
    attachment when there's only one, and require `field=Post.topics` to disambiguate otherwise.

    `tag_model_name`/`through_model_name` override the generated models' class names (default
    `<Host><Attr>Tag` and `<Host><Attr>ObjectTag`). They also name the tables, so settle them before
    the first migration.

    `tag_model=` binds a tag model you wrote - see `AbstractTag` - and `through_model=` (with it) a
    through model; the options that describe the model it replaces are refused beside it.
    See docs/django/apps/tags/tags.md.
    """
    return _TagsField(
        related_name=related_name,
        target_name=target_name,
        target_related_name=target_related_name,
        name_max_length=name_max_length,
        name_validators=name_validators,
        normalize=normalize,
        tag_base_model=tag_base_model,
        through_base_model=through_base_model,
        tag_extra_fields=tag_extra_fields,
        through_extra_fields=through_extra_fields,
        tag_model_name=tag_model_name,
        through_model_name=through_model_name,
        tag_model=tag_model,
        through_model=through_model,
    )


def with_tag_usage(queryset, field):
    """
    `queryset` - of the tag model behind `field` (`Post.topics`) - with `usage_count`, how many of the
    host's rows carry each tag through `field`, as one subquery, so a list of tags costs one query:

        with_tag_usage(Post.topics.model.objects.all(), Post.topics).order_by("-usage_count")

    A pool several hosts share is counted for `field`'s host only.
    """
    tag_key = field.field.m2m_reverse_field_name()
    uses = field.through._default_manager.filter(**{tag_key: OuterRef("pk")}).order_by()
    return queryset.annotate(
        usage_count=Coalesce(Subquery(uses.values(tag_key).annotate(n=Count("pk")).values("n")), 0)
    )


class TaggableMixin:
    """Mix into a host model to manage tags on itself where `tags()` is attached - see `tags`."""

    def add_tag(self, name, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        tag = field.model.objects.get_tag(name)
        getattr(self, field.config.attname).add(tag)
        return tag

    def remove_tag(self, name, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        tag = field.model.objects.filter(name=field.model.objects.normalized(name)).first()
        if tag:
            getattr(self, field.config.attname).remove(tag)

    def set_tags(self, names, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        tags_ = [field.model.objects.get_tag(n) for n in names]
        getattr(self, field.config.attname).set(tags_)

    def tag_names(self, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        return getattr(self, field.config.attname).to_list()
