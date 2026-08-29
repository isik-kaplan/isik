"""tags() maker + TaggableMixin - see `tags` for usage."""

from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction

from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose,
    resolve_base_model,
    resolve_field,
)


TAG_NAME_REGEX = r"^[a-zA-Z0-9_.-]*$"
TAG_NAME_REGEX_ERROR_MESSAGE = "Tags can only contain letters, numbers, - and _."


def _default_name_validators():
    return [RegexValidator(TAG_NAME_REGEX, TAG_NAME_REGEX_ERROR_MESSAGE)]


class TagQuerySet(models.QuerySet):
    def to_list(self):
        """Plain list of tag name strings - e.g. what `generic_tag_field()` reads back."""
        return list(self.values_list("name", flat=True))

    def get_tag(self, name):
        """Get-or-create by name, applying the maker's `normalize=` first if configured, then
        enforcing the `name` field's validators (e.g. `name_validators`) - `create()` bypasses
        `full_clean()`, so without this an invalid name would otherwise slip through untouched."""
        normalize = getattr(self.model, "_normalize", None)
        if normalize:
            name = normalize(name)
        self.model._meta.get_field("name").run_validators(name)
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


class _TagsField:
    """Descriptor returned by `tags()` - see `tags` for the public docstring."""

    def __init__(
        self,
        *,
        related_name,
        target_name,
        target_related_name,
        name_max_length,
        name_validators=None,
        normalize=None,
        tag_base_model=None,
        through_base_model=None,
        tag_extra_fields=None,
        through_extra_fields=None,
    ):
        self.related_name = related_name
        self.target_name = target_name
        self.target_related_name = target_related_name
        self.name_max_length = name_max_length
        self.name_validators = name_validators if name_validators is not None else _default_name_validators()
        self.normalize = normalize
        self.tag_base_model = tag_base_model
        self.through_base_model = through_base_model
        self.tag_extra_fields = tag_extra_fields or {}
        self.through_extra_fields = through_extra_fields or {}
        self.attname = None  # pragma: no mutate

    def contribute_to_class(self, host_cls, name):
        self.attname = name  # pragma: no mutate
        tag_model_name = f"{host_cls.__name__}{name.capitalize()}Tag"
        through_model_name = f"{host_cls.__name__}{name.capitalize()}ObjectTag"
        claim_related_name(host_cls, self.target_related_name, through_model_name)

        tag_fields = {
            "name": models.CharField(max_length=self.name_max_length, unique=True, validators=self.name_validators),
            "objects": TagManager(),
            **self.tag_extra_fields,
        }
        tag_model = build_model(
            tag_model_name,
            host_cls,
            fields=tag_fields,
            base_model=resolve_base_model(self.tag_base_model, "TAGS_TAG_BASE_MODEL"),
            extra_attrs={"_normalize": self.normalize} if self.normalize else None,
        )

        through_fields = {
            "tag": models.ForeignKey(tag_model, on_delete=models.CASCADE),
            self.target_name: models.ForeignKey(
                host_cls, on_delete=models.CASCADE, related_name=self.target_related_name
            ),
            **self.through_extra_fields,
        }
        through_model = build_model(
            through_model_name,
            host_cls,
            fields=through_fields,
            base_model=resolve_base_model(self.through_base_model, "TAGS_THROUGH_BASE_MODEL"),
            meta_attrs={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["tag", self.target_name], name=f"unique_{through_model_name.lower()}"
                    )
                ]
            },
        )

        m2m_field = models.ManyToManyField(tag_model, through=through_model, related_name=self.related_name, blank=True)
        m2m_field.contribute_to_class(host_cls, name)
        descriptor = getattr(host_cls, name)
        expose(host_cls, name, descriptor, generated_model=tag_model, config=self)


def tags(
    *,
    related_name,
    target_name="target",
    target_related_name="tags",
    name_max_length=100,
    name_validators=None,
    normalize=None,
    tag_base_model=None,
    through_base_model=None,
    tag_extra_fields=None,
    through_extra_fields=None,
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

    `name_max_length`/`name_validators` control the generated `name` field (default: letters,
    digits, `-`/`_`, via `RegexValidator`). `normalize` is an optional callable (e.g. `str.lower`)
    applied to a name before every dedup lookup/create - off by default, so `"Python"` and
    `"python"` are different tags unless you opt in.

    `tag_base_model`/`through_base_model` (or the `TAGS_TAG_BASE_MODEL`/`TAGS_THROUGH_BASE_MODEL`
    settings) let the two generated models inherit your own base. `tag_extra_fields`/
    `through_extra_fields` merge in extra fields (or other makers) onto the Tag model and the
    through model respectively.

    A host can attach `tags()` more than once - `TaggableMixin` methods auto-pick the sole
    attachment when there's only one, and require `field=Post.topics` to disambiguate otherwise.
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
        tag = field.model.objects.filter(name=name).first()
        if tag:
            getattr(self, field.config.attname).remove(tag)

    def set_tags(self, names, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        tags_ = [field.model.objects.get_tag(n) for n in names]
        getattr(self, field.config.attname).set(tags_)

    def tag_names(self, *, field=None):
        field = resolve_field(self, field, _TagsField, "taggable")
        return getattr(self, field.config.attname).to_list()
