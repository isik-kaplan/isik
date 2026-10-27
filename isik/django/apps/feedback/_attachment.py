"""
What `votes()`, `comments()`, `notes()` and `bookmarks()` share: an attachment that builds its model on
the host, or - given `model=` - takes a model the project wrote, checks it against what the
attachment reads of it, and exposes it the same way. Either way `Host.<name>` is Django's own
reverse accessor, carrying `.model` and `.config` (the attachment).
"""

from weakref import WeakKeyDictionary

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce

from isik._internal.translation import gettext as _
from isik.django.apps.common._contracts import Contract, Need, kind, the_foreign_key_to
from isik.django.apps.common._model_makers import (
    build_model,
    claim_related_name,
    expose,
    expose_via_reverse_accessor,
    keyword_list,
    merge_fields,
    resolve_base_model,
    when_registered,
)


# Each attachment's config by its model - how a vote, comment, note or bookmark finds the attachment
# it belongs to.
_configs = WeakKeyDictionary()


def config_of(model):
    """The attachment `model` - a feedback model class - belongs to."""
    try:
        return _configs[model]
    except KeyError:
        raise TypeError(
            _("%(model)s isn't a model of votes(), comments(), notes() or bookmarks()") % {"model": model.__name__}
        ) from None


def has_field(model, name):
    return any(field.name == name for field in model._meta.get_fields())


class FeedbackAttachment:
    """One `votes()`/`comments()`/`notes()`/`bookmarks()` on a host - see each maker's docstring."""

    # Set by each kind.
    maker = None
    noun = None
    base_setting = None
    default_related_name = None
    unique_per_user = False
    # The timestamps its serializer factory reads.
    timestamps = ("created_at",)

    def __init__(self, *, model, target_name, generating):
        """`generating` holds the options that only describe a model this builds - refused beside
        `model=`, which is used as it is."""
        given = sorted(option for option, value in generating.items() if value is not None)
        if model is not None and given:
            raise TypeError(
                _("%(maker)s(model=...) uses the model as it is - drop %(options)s")
                % {"maker": self.maker, "options": keyword_list(given)}
            )
        if model is None and generating["user_related_name"] is None:
            raise TypeError(_("%(maker)s needs user_related_name= unless model= is given") % {"maker": self.maker})
        self.model_ref = model
        self.target_name = target_name
        self.user_related_name = generating["user_related_name"]
        self.target_related_name = generating["target_related_name"] or self.default_related_name
        self.user_model = generating["user_model"] or settings.AUTH_USER_MODEL
        self.base_model = generating["base_model"]
        self.extra_fields = generating["extra_fields"] or {}
        self.model_name = generating["model_name"]

    def contribute_to_class(self, host_cls, name):
        self.host = host_cls
        self.attname = name
        if self.model_ref is None:
            self.generate(host_cls, name)
        else:
            when_registered(host_cls, self.model_ref, lambda model: self.bind(host_cls, name, model))

    def generate(self, host_cls, name):
        self.target_name = self.target_name or "target"
        model_name = self.model_name or f"{host_cls.__name__}{name.capitalize()}{self.noun}"
        claim_related_name(host_cls, self.target_related_name, model_name)
        claim_related_name(self.user_model, self.user_related_name, model_name)
        target_text, user_text = self.descriptions(host_cls)
        fields = {
            self.target_name: models.ForeignKey(
                host_cls,
                on_delete=models.CASCADE,
                related_name=self.target_related_name,
                help_text=target_text[0],
                db_comment=target_text[1],
            ),
            "user": models.ForeignKey(
                self.user_model,
                on_delete=models.CASCADE,
                related_name=self.user_related_name,
                help_text=user_text[0],
                db_comment=user_text[1],
            ),
            **self.own_fields(),
        }
        meta_attrs = {}
        if self.unique_per_user:
            meta_attrs["constraints"] = [
                models.UniqueConstraint(fields=[self.target_name, "user"], name=f"unique_{model_name.lower()}_per_user")
            ]
        generated_model = build_model(
            model_name,
            host_cls,
            fields=merge_fields(fields, self.extra_fields, model_name),
            base_model=resolve_base_model(self.base_model, self.base_setting),
            meta_attrs=meta_attrs,
        )
        _configs[generated_model] = self
        expose_via_reverse_accessor(
            host_cls, name, self.target_related_name, generated_model=generated_model, config=self
        )

    def bind(self, host_cls, name, model):
        key = the_foreign_key_to(model, host_cls, self.target_name, self.maker)
        self.target_name = key.name
        self.contract().check(model)
        if key.remote_field.hidden:
            raise ImproperlyConfigured(
                _("%(model)s can't be %(maker)s's model: its %(field)s needs a related_name that isn't hidden.")
                % {"model": model._meta.label, "maker": self.maker, "field": key.name}
            )
        _configs[model] = self
        expose(
            host_cls, name, getattr(host_cls, key.remote_field.get_accessor_name()), generated_model=model, config=self
        )

    def contract(self):
        needs = [
            Need("id", _("a primary key named id"), lambda found: found.primary_key),
            kind("user", models.ForeignKey, _("a ForeignKey to the user model")),
            *(kind(name, models.DateTimeField, _("a DateTimeField")) for name in self.timestamps),
            *self.own_needs(),
        ]
        unique = ((self.target_name, "user"),) if self.unique_per_user else ()
        return Contract(maker=self.maker, needs=tuple(needs), optional=tuple(self.optional_needs()), unique=unique)

    @property
    def model(self):
        """The model its rows live in - built here, or the project's own."""
        return getattr(self.host, self.attname).model

    def rows(self):
        """Its rows on the host row of the query this is a subquery of."""
        return self.model._default_manager.filter(**{self.target_name: OuterRef("pk")}).order_by()

    def count(self, rows):
        """How many of `rows` there are - 0, not null, for none."""
        return Coalesce(Subquery(rows.values(self.target_name).annotate(n=Count("pk")).values("n")), 0)

    def annotations(self, user):
        """`{name: expression}` - what `with_feedback()` annotates the host's rows with, named
        `<attname>_<what>`. `user` is the one asking - None, or anonymous, for nobody."""
        signed_in = user is not None and user.is_authenticated
        return {f"{self.attname}_{what}": expression for what, expression in self.own_annotations(signed_in, user)}

    # Each kind's own.

    def own_annotations(self, signed_in, user):
        """`(what, expression)` pairs - see `annotations()`."""
        raise NotImplementedError

    def descriptions(self, host_cls):
        """`((help_text, db_comment) for the host key, (help_text, db_comment) for user)`."""
        raise NotImplementedError

    def own_fields(self):
        return {}

    def own_needs(self):
        return ()

    def optional_needs(self):
        return ()
