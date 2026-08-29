"""
Concrete models used only to exercise isik's Django helpers against a real
database - a real BaseModel subclass, a real many-to-many through a
non-auto_created table, and a real AutoGenericForeignKey.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django_lifecycle import AFTER_CREATE, AFTER_SAVE, AFTER_UPDATE, BEFORE_CREATE, BEFORE_SAVE, BEFORE_UPDATE, hook

from isik.django.apps.common.db import BaseModel, ContextField, track_events
from isik.django.apps.common.fields.gfk import AutoGenericForeignKey
from isik.django.apps.feedback.bookmarks import UserBookmarkMixin, bookmarks
from isik.django.apps.feedback.comments import UserCommentMixin, comments
from isik.django.apps.feedback.notes import UserNoteMixin, notes
from isik.django.apps.feedback.votes import UserVoteMixin, votes
from isik.django.apps.tags.tags import TaggableMixin, tags
from isik.django.apps.templated_fields import TemplateCharField, TemplatePolicy, TemplateTextField


def positive_only(value):
    if value < 0:
        raise ValidationError("Must be positive.")


class EmailUser(UserVoteMixin, UserBookmarkMixin, UserNoteMixin, UserCommentMixin, AbstractUser):
    """A real AUTH_USER_MODEL with USERNAME_FIELD != "username", for UsernameOREmailModelBackend tests."""

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    email = models.EmailField(unique=True)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")

    class Meta:
        app_label = "testapp"


@track_events()
class Widget(BaseModel):
    name = models.CharField(max_length=100)
    count = models.IntegerField(default=0, validators=[positive_only])
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        app_label = "testapp"


@track_events(
    context_fields=[
        ContextField(
            "actor",
            context_key="user",
            cast="bigint",
            field=models.ForeignKey(
                settings.AUTH_USER_MODEL, null=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+"
            ),
        ),
        # No FK target of its own on purpose - stands in for a value with nowhere to point (e.g.
        # a schema/tenant name string rather than a row), still stamped the same way.
        ContextField("actor_schema", context_key="schema", cast="text", field=models.TextField(null=True)),
        ContextField(
            "tenant",
            context_key="organization",
            cast="bigint",
            field=models.ForeignKey(
                settings.AUTH_USER_MODEL, null=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+"
            ),
        ),
    ],
    meta={"indexes": [models.Index(fields=["tenant", "actor"], name="ctx_widget_tenant_actor_idx")]},
)
class ContextTrackedWidget(BaseModel):
    """Exercises `ContextField` - real, indexed `actor`/`actor_schema`/`tenant` columns on its
    event model, stamped from pghistory's own request context instead of a JSON lookup, plus a
    composite index across two of them (`meta={"indexes": [...]}` composes with `context_fields=`
    for free - both just feed the same `create_event_model()` call)."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class WidgetProfile(BaseModel):
    """Reverse-O2O counterpart to Widget, for exercising FlattenedOneToOneMixin against a real
    one-to-one relation instead of a hand-built one."""

    widget = models.OneToOneField(Widget, on_delete=models.CASCADE, related_name="profile")
    bio = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "testapp"

    def clean(self):
        # An object-level check, invisible to DRF's automatic per-field validation - exercises
        # FlattenedOneToOneMixin's Django ValidationError -> DRF ValidationError translation for
        # a failure that can only surface via full_clean(), not serializer.is_valid().
        if self.bio == "banned":
            raise ValidationError({"bio": "This bio is not allowed."})


class WidgetSettings(BaseModel):
    """Second reverse-O2O counterpart to Widget - only used to exercise
    FlattenedOneToOneMixin's field-name-collision check against two independent accessors."""

    widget = models.OneToOneField(Widget, on_delete=models.CASCADE, related_name="settings")
    bio = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "testapp"


class Tag(BaseModel):
    label = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class WidgetTag(models.Model):
    """Explicit through model - not auto_created, unlike a plain ManyToManyField's default."""

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    note = models.CharField(max_length=100, blank=True)

    class Meta:
        app_label = "testapp"


class TaggedWidget(BaseModel):
    name = models.CharField(max_length=100)
    tags = models.ManyToManyField(Tag, through=WidgetTag, related_name="tagged_widgets")

    class Meta:
        app_label = "testapp"


class Note(BaseModel):
    body = models.CharField(max_length=200)
    target = AutoGenericForeignKey(limit_models_to=[Widget])

    class Meta:
        app_label = "testapp"


class Comment(BaseModel):
    """A real on_delete=PROTECT relationship, for exercising ProtectedDestroyMixin against a
    genuine ProtectedError instead of a hand-built one."""

    body = models.CharField(max_length=200)
    widget = models.ForeignKey(Widget, on_delete=models.PROTECT, related_name="comments")

    class Meta:
        app_label = "testapp"


class Recorder(BaseModel):
    """Records the order lifecycle hooks fire in, to pin down BaseModel.save()'s contract."""

    STR = "Recorder<{self.name}>"

    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        app_label = "testapp"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hook_log = []

    @hook(BEFORE_CREATE)
    def _before_create(self):
        self.hook_log.append("before_create")

    @hook(AFTER_CREATE)
    def _after_create(self):
        self.hook_log.append("after_create")

    @hook(BEFORE_UPDATE)
    def _before_update(self):
        self.hook_log.append("before_update")

    @hook(AFTER_UPDATE)
    def _after_update(self):
        self.hook_log.append("after_update")

    @hook(BEFORE_SAVE)
    def _before_save(self):
        self.hook_log.append("before_save")
        # Deliberately mutates a field the caller didn't pass to update() - exercises
        # BaseModel.update_fields widening around hook-driven side effects.
        self.slug = self.name.lower()

    @hook(AFTER_SAVE)
    def _after_save(self):
        self.hook_log.append("after_save")


class Post(TaggableMixin, BaseModel):
    """Exercises votes()/bookmarks()/notes()/comments()/tags() - a plain-text host for the
    feedback+tags apps, including votes() attached to comments via extra_fields rather than
    automatically, and two independent tags() dimensions to exercise multi-attachment."""

    title = models.CharField(max_length=100)

    # `comments` is declared before `votes`/`bookmarks`/`notes` deliberately: it calls votes()
    # again for its own extra_fields, and `votes = votes(...)` below would otherwise shadow the
    # imported function within this same class body once assigned.
    comments = comments(
        user_related_name="post_comments",
        extra_fields={"votes": votes(user_related_name="post_comment_votes")},
    )
    votes = votes(user_related_name="post_votes")
    bookmarks = bookmarks(user_related_name="post_bookmarks")
    notes = notes(user_related_name="post_notes", base_model=BaseModel)
    # Two independent tag pools on the same host - target_related_name must differ (the default
    # "tags" would otherwise clash between them on Post's own reverse-accessor namespace).
    topics = tags(related_name="posts_with_topic")
    labels = tags(related_name="posts_with_label", target_related_name="label_object_tags")

    class Meta:
        app_label = "testapp"


class Article(BaseModel):
    """A second, separate commentable host - real AUTH_USER_MODEL FK related_names must not clash
    with Post's, and this one opts into tiptap=True instead of plain text."""

    title = models.CharField(max_length=100)

    comments = comments(
        user_related_name="article_comments",
        tiptap=True,
        comment_min_length=1,
        comment_max_length=280,
    )

    class Meta:
        app_label = "testapp"


class MultiVoteHost(BaseModel):
    """Two independent votes() attachments on the same host, real-DB backed - proves any maker
    (not just tags()) supports multiple attachments via the field= disambiguation mechanism."""

    upvotes = votes(user_related_name="multi_host_upvotes", target_related_name="upvotes")
    helpfulness = votes(user_related_name="multi_host_helpfulness", target_related_name="helpfulness")

    class Meta:
        app_label = "testapp"


def default_text_context(obj, request=None):
    return {"title": obj.title, "me": getattr(request, "me", None)}


class TemplatedPost(BaseModel):
    """Exercises TemplateCharField/TemplateTextField - templated_fields is a plain custom field
    type (no per-attachment generated model, unlike the feedback/tags makers above)."""

    title = models.CharField(max_length=100)
    default_text = TemplateTextField(available=default_text_context, blank=True)
    greeting = TemplateCharField(
        max_length=200, available=default_text_context, policy=TemplatePolicy.VARIABLES_ONLY(), blank=True
    )

    class Meta:
        app_label = "testapp"
