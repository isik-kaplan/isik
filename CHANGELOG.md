# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.36.0] - 2026-10-26

### Added

- **`model=` on `votes()`, `comments()`, `notes()` and `bookmarks()`** binds a model the project wrote - a
  class or `"app_label.Model"`, written before or after its host - instead of building one. It's checked
  once registered against what the maker reads of it (its fields, its key to the host, its unique field
  sets), with every problem raised in one `ImproperlyConfigured`. The options that describe a built model
  are refused beside it. **`tags(tag_model=..., through_model=...)`** does the same for a tag pool - which
  several hosts can then share - with **`AbstractTag`** as a base for the tag model.
- **`with_feedback(queryset, user)`** annotates a host's rows with each attachment's `_score`, `_up`,
  `_down`, `_mine` (votes), `_count` (comments, bookmarks), `_mine` (bookmarks) and `_mine_count` (notes) -
  one subquery each, so a list is one query. **`ranked(queryset, Post.votes, by=...)`** orders by
  `score`, `wilson` (the lower bound of the 95% Wilson interval) or `controversial`, computed in the query.
  **`VotesField`, `BookmarksField`, `CommentCountField` and `NoteCountField`** read them in a serializer.
- **`VoteActions`, `BookmarkActions`, `CommentActions` and `NoteActions`** add a host's feedback routes to
  its own viewset - `PUT`/`DELETE {id}/vote/` and `{id}/bookmark/`, a page of `{id}/comments/` and
  `{id}/notes/` with create, edit and delete - fetching the host with the view's own `get_object()`,
  checked with `feedback_permission_classes`, and exempt from isik's write guards where they don't apply.
- **Threaded comments**: `comments(threaded=True, max_depth=..., track_edits=True)` adds `parent` and
  `edited_at` - a bound model has them by having the fields - with `UserCommentMixin.reply()`,
  `edit_comment()` and `delete_comment()`, `reply_count`, and `?parent=` / `?top_level=` on the comments
  route. Threads come back flat.
- **`with_tag_usage(queryset, field)`** and **`TagUsageField`** - tag usage counts as one subquery;
  `generic_tag_serializer()`'s `usage_count` reads them.
- **`schema_actions` and `schema_list_actions`** on a view tell isik's `AutoSchema` an action's request,
  response, status and parameters, and which GETs answer with a page - read when the schema is
  generated, unlike `@extend_schema`.

### Changed

- **Breaking: a `base_model`'s Meta reaches the model a maker builds** - its `ordering`, `indexes`,
  `constraints` and the rest, which were dropped. A project whose base declares Meta options gets a
  migration for them.
- **Breaking: `extra_fields` can't replace a field the maker builds** - an extra field named `value`,
  `body`, `user` or the host key raises `TypeError` instead of silently replacing it.
- **Breaking: generic_tag_serializer()'s `usage_count` is a `TagUsageField`**, not a
  `ModelRelatedCountField` - the same count, read from `with_tag_usage()` when the queryset has it.
- `user_related_name` is refused beside `model=` and needed without it, with a message saying so.

### Fixed

- **`TaggableMixin.remove_tag()`** normalizes the name as `add_tag()` does, so a tag added as `"Python"`
  under `normalize=str.lower` is removed by `"Python"` too.

## [0.35.1] - 2026-10-25

### Fixed

- **A current value a caster makes into None** - `"any"` for no limit - is kept as that value, and as its
  question's default, instead of reading as no current value and giving way to the default. Its question
  shows it by its own text - `Uses [any]` - and Enter keeps it, a required question's too, instead of
  asking again.

## [0.35.0] - 2026-10-25

### Added

- **`options(errors=ErrorStyle.MESSAGE)`** shows a value the command can't use as its message alone, on
  stderr with exit code 2, instead of click's usage error. `ErrorStyle.USAGE` stays the default.

## [0.34.0] - 2026-10-25

### Added

- **`Requirement` - how much a rule asks of an opt-out**: `STRICT` (a reason over the floor, the default),
  `OPTIONAL` (no reason, or the slot's plain opt-out value: `exempt_from_registry=True`,
  `{"destroy": True}`, a setting's `question=False`), or `OFF` (a check asks nothing; refused for a rule
  whose exemption changes behavior). A rule's owner gives the default, `exemption_class(...,
  requirement=...)`, and a project sets any rule's level, its own or isik's, in `ISIK_EXEMPTIONS` or with
  `exemption_requirements()`. A project's checks honor it through `opted_out()` and `enforced()`. A
  plain opt-out is still made into an exemption, listed and counted. An exemption is always truthy
  unless its type shows as something else.
- **`isik.cli.is_interactive()`** - whether the running command asked its questions, for the decisions it
  makes itself afterwards.
- **`HelpFormat(default_separator=..., option_file_help=...)`** - the text between a description and its
  default, and the help of a `--x-file` option, each worded the app's own way.

### Changed

- **A view's exemption maps are copies once its class is made**: `precondition_exempt_actions`,
  `idempotency_*_actions`, `unguarded_fields` and the like hold the same exemptions, with any plain opt-out
  made into its exemption, but they're no longer the dict the class body wrote.

### Fixed

- **`options()` on a click group** keeps the group's `Commands:` section in `--help`.
- **A questionary question with an unset default** shows its `shown` text in the question -
  `Lock page [the server's]` - rather than typed into the answer, where it had to be deleted first. A
  flag with an unset default says so in its question too, in either prompt style.

## [0.33.0] - 2026-10-25

### Added

- **`isik.common.settings`** - settings declared once, as class attributes, with no dependencies:

  ```python
  class Backup(Settings, prefix="BACKUP_"):
      level = Setting(int, "Compression level", default=3, min=1, max=22, soft_max=19)
      password = Setting(str, "Encryption password", secret=True, default="")
  ```

  A `Settings` subclass is a frozen dataclass, made in code or `from_env()`, and each value knows its
  `source()`. Values come from option, environment variable (and `_FILE`), answer, current value, then
  default. Settings can have:

  - hard and soft limits;
  - lazy and `UNSET` defaults;
  - conditional settings (`only_when`, `ask_when`) and relations (`requires`, `excludes`);
  - `check()` across settings, run on every instance;
  - groups, including over a plain dataclass (`Group.from_dataclass`), selectors that pick a group, and
    constants;
  - messages a class can reword.

  An opt-out is an exemption with a reason: `NoOption`, `NoEnv`, `NoQuestion`, `AskedAtUse`,
  `ReplacedByFlow`. A class's `NoEnv` covers every group it nests. Two settings that would share an
  option or a variable, `_FILE` and `--no-x` forms included, are refused when the class is defined.
- **`isik.cli`** (the new `cli` extra: click, questionary) - `@options(Backup)` puts a class's settings
  on a click command:
  - **Options:** `--x/--no-x`, repeatable, bare-valued, `--x-file`. A selector's chosen group decides
    the options, read before parsing.
  - **Questions:** asked per policy (`terminal_unless_no_input()` with `--no-input`, or
    `no_option_given()`), in click's or questionary's style per setting.
  - **Help:** `HelpFormat` lays it out.
  - **`isik.cli.testing`:** `assert_reachable()` (every setting by option, variable and question, in
    every selector combination, and no plain click options), `answers()`, `questionary_io()`,
    `settings_table()` and `assert_docs_current()`.
- **`natural_boolean`** - a caster taking yes/no, on/off, true/false and 1/0, in any case.
- **`py.typed`** - editors and type checkers read isik's own annotations.
- **`config(environ=..., files=True)`** - reads a given mapping instead of `os.environ`, and `KEY_FILE`
  as the file holding KEY's value. Files are off by default, since a schema may have a `KEY_FILE`
  setting of its own.

## [0.32.0] - 2026-10-24

### Added

- **`HistoryMixin.history_serializer_bases`** - serializer classes mixed into the history serializer a
  view builds, for fields every row shows that isik doesn't (an actor's label, resolved once per page
  through `history_page_context()`). The class keeps the built one's name and is cached with it under
  `history_serializer_cache_key()`, so a project no longer rebuilds or caches it beside isik's. Over
  several streams, a field it adds keeps its own nullability - only the owner's fields are null on
  another stream's row.

## [0.31.0] - 2026-10-24

### Fixed

- **`HistoryMixin`'s `actor` filter over an `actor` `ContextField` follows the column's type.** 0.30.0
  typed the JSON `actor` filter after the user model's pk, but the `ContextField` branch - the one
  `context_field_filter()` recommends - stayed a number, so `?actor=<uuid>` was refused for a UUID-keyed
  actor. `context_field_filter()` without `filter_cls` now takes django-filter's filter for its column's
  own type, a foreign key's being that of what it points at.
- The idempotency mixin's docs, garbled since 0.29.0 where the `idempotency_replayed_header` row ran
  into the paragraph after it.

### Changed

- **Breaking: `ISIK_CHECKS` is written with enums, never strings.** `Check`, `CheckOption` and `Severity`
  (`isik.django.apps.common.checks`) name each check, each option and each severity:

  ```python
  ISIK_CHECKS = {Check.DOCUMENTATION: {CheckOption.SEVERITY: Severity.WARNING}}
  ```

  They're plain `Enum`s, so a string never stands in for one: a string key, option or severity is an
  error, `isik.E012`, naming the member to write - `ISIK_CHECKS names 'documentation' as a string - write
  Check.DOCUMENTATION.` A project replaces each string in its `ISIK_CHECKS` with the member of that
  name. `check_options()` and `isik_check()` take a `Check`, and `check_options()` returns a dict keyed
  by `CheckOption`.

## [0.30.1] - 2026-10-24

### Fixed

- **`DeadSessionCookieMiddleware` no longer deletes a live session's cookie.** It deleted the cookie on
  any 401/403 to a request that wasn't signed in - and a sign-in halfway through (a second factor, a
  re-authentication, a provider sign-up) answers 401 while its session holds the step to come back to,
  so the second step could never complete. A cookie is now dead only when its session is no longer in
  the configured `SESSION_ENGINE`'s store - `session_exists(key)`; a signed-cookie session exists while
  its cookie still loads.

### Added

- The `documentation` check (`isik.E010`) takes `inherited_from_libraries`: `True` by default, so every
  field is checked as before; `False` leaves out the fields a model inherits unchanged from an installed
  library's abstract base - `AbstractUser`'s, an OAuth application's, a tenant mixin's - whose
  description is the library's to write. A field the project declares again is still checked.
- `is_library_path(path)` (`isik.django.apps.common.project`).

## [0.30.0] - 2026-10-23

### Added

- **isik's system checks are tuned or turned off by name in `ISIK_CHECKS`.** Each check has a name
  (`idempotency`, `documentation`, ...) and takes `enabled`, `severity` (`"error"` or `"warning"`), and,
  by what it reads, `urlconfs` (every urlconf the project serves by default) or `apps` (the project's
  own apps by default). A `urlconfs` leaving a served urlconf out is a warning, `isik.W003`, naming it;
  a name or option that doesn't exist is an error, `isik.E012`. See `docs/django/apps/common/checks.md`.
- **New system checks, on by default:**
  - `isik.E005` (`idempotency`): a routed write that honors no `Idempotency-Key`.
  - `isik.E006` (`singletons`): a `FlattenedSingletonRelatedMixin` declaration no unique constraint
    proves.
  - `isik.E007` (`preconditions`): a routed write to one object that asks no `If-Match`.
  - `isik.E008` (`history_indexes`): an event model with a `pgh_obj` but no `(pgh_obj, -pgh_id)` index.
  - `isik.E009` (`exemptions`): an exemption call or type loading the project didn't see - what
    `manage.py exemptions` fails on, now at `manage.py check` too.
  - `isik.E010` (`documentation`): a field of the project's own models without `help_text`, or without
    `db_comment` where it has a column. An `Exemption` given as either answers for it; `exemption_types`
    narrows which.
  - `isik.E011` (`not_atomic`): a routed view served outside `ATOMIC_REQUESTS` (`non_atomic_requests`)
    without saying why.
- **`not_atomic(reason, using=None)`** (`isik.django.apps.common.atomic`) - `non_atomic_requests` with
  its reason, a `NotAtomic` exemption (rule `isik.not-atomic`), on a function view or a class-based
  view's class.
- **`FlattenedSingletonRelatedMixin`** and `SingletonRelated(accessor, match, attribute,
  on_empty="delete")` - one row among a parent's many related ones (the primary domain), read as a field
  of the parent and written through to it: the matching row is locked and saved narrowly, created from
  `match` when missing (a lost race is a 400), and deleted, cleared or refused on an empty value.
- **A history built from several streams.** `HistoryMixin.history_also = {key: HistoryStream(model,
  path=..., serializer=...)}` takes other models' events into an object's history, each row shown
  through its own stream's serializer and carrying `part`, `subject_id`, `subject_label` and `detail`
  (`history_detail = {key: (foreign key, attribute)}`). Scoped to the declared paths, ordered by time,
  filtered by `part` and `object_id`, refused when first routed if a declaration can't be served
  (`history_part_problems(view)`), and documented without a drf-spectacular warning.
- `HistoryMixin.history_page_context(rows)` - what a page's rows need resolved once, handed to the
  serializer as `context["history_page"]`; `history_serializer_cache_key()` - a serializer per key.
- `track_events(trackers=..., condition=..., append_only=...)`; `context_fields_trigger()`, public;
  `history_index_problems()`.
- `uuid7()` and `new_primary_key()`: `UUIDPrimaryKeyModel.id` defaults to the function
  `ISIK_PRIMARY_KEY_DEFAULT` names (`"uuid.uuid4"` unless set).
- `generated_models(kind=None)` and `mark_generated()`: the models isik generates (`"event"`, `"maker"`,
  `"lock"`), each marked `isik_generated`.
- `enum_name_overrides(own=None)` (`isik.django.drf.spectacular`) - a lazy `ENUM_NAME_OVERRIDES` naming
  the generated models' enums, so their `value` and a project's don't collide; the project's own win.
- `SpectacularAPIView` (`isik.django.drf.schema_views`) - the schema in `ISIK_SCHEMA_LANGUAGE`, whatever
  the request's language.
- `AutoSchema` documents `Idempotency-Key`, and an idempotent operation's 409 and 422.
- `DeadSessionCookieMiddleware` - a 401/403 to a request with a dead session cookie deletes it and says
  so in `Session-Expired` (`DEAD_SESSION_HEADER`).
- `HistoryContextMiddleware` offers `version` and `request_id`, named in `HISTORY_CONTEXT_KEYS`.
- `HistoryContextTask.dispatch_on_commit` (or `HISTORY_TASK_DISPATCH_ON_COMMIT`) - sent once the
  dispatching transaction commits.
- `RequestPolicy.succeeded(request, view, response)` - runs on a 2xx to a request the policy allowed.
- `MatchModeFilter` - a list of values over a to-many relation, matched by any or all of them.
- `reading(request)`, `ReadOnly`'s predicate.
- `with_python_defaults(instance)` - an unsaved instance's `db_default` fields given their values in
  Python.
- `project_app_configs(labels=None)` - the installed apps that are the project's own.

### Changed

- **Breaking: `BaseModelViewSet` carries `IdempotencyMixin` and `PreconditionMixin`.** Every model
  viewset's writes ask an `Idempotency-Key` (POST, PUT, PATCH, DELETE), and its writes to one object an
  `If-Match`. A project must:
  - turn on `ATOMIC_REQUESTS` and install an idempotency claims app
    (`isik.django.apps.idempotency.by_reference` or `.with_body`);
  - take `IdempotencyMixin`/`PreconditionMixin` out of the bases of its `BaseModelViewSet` subclasses -
    naming either again is an MRO error;
  - give a view that mustn't ask them `idempotent_methods = ()` / `precondition_methods = ()`, or exempt
    its actions in `idempotency_exempt_actions` / `precondition_exempt_actions`;
  - send both from its clients. A write without a key is now a 400 before an object permission's 403.
- **Breaking: `IdempotencyMixin.idempotent_methods` is POST, PUT, PATCH and DELETE** (was POST): a retried
  update or delete replays its first answer rather than running again. `idempotency_coverage()`'s default
  follows.
- **Breaking: `IDEMPOTENCY_LOCK_TIMEOUT` defaults to 10 seconds** (was unbounded): a retry waiting on a
  first request that holds its claim longer gets a 409 (`idempotency_key_in_flight`).
- **Breaking: `PRECONDITION_IGNORES_WEAK_TAGS` defaults to False** - RFC 9110's strong comparison, where
  `If-Match: W/"..."` never matches. Turn it on where something between server and client weakens the
  tag (`GZipMiddleware`, nginx's gzip).
- **Breaking: a 412 shows `current` and the `ETag` only to a caller who may read the row** - the view's
  `retrieve` permissions, `get_queryset()` and filters, and object permissions. Otherwise `current` is
  null and there's no `ETag`.
- **Breaking: a request policy's refusal body is `{"detail": ..., "code": ...}`** (was `{"detail"}`).
- **Breaking: the common app's label is `isik_common`** (was `common`), freeing `common` for a project's
  own app. A project naming it by label (`apps.get_app_config("common")`) names `isik_common`. It has no
  models or migrations.
- **Breaking: `UUIDPrimaryKeyModel.id`'s default is `new_primary_key`** (was `uuid.uuid4`). Run
  `makemigrations`: one `AlterField` per model, changing nothing in the database.
- **Breaking: a `ContextField` named like a field the history tracks is refused** (`ImproperlyConfigured`)
  - the event model held the context's value where the row's own was.
- **Breaking: isik's system checks run the new checks, as errors, on every project.** Fix what they
  report, or turn a check off in `ISIK_CHECKS`. The `documentation` check (`isik.E010`) is the one most
  projects will meet first.
- **Breaking: `PreconditionMixin` no longer overrides `get_serializer()`** - which made drf-spectacular
  read every view's serializer through it - and `precondition_serializer` is gone. A create's row is read
  from the response's serializer.
- **Breaking: `_context_fields_attrs_and_trigger()` is `context_fields_trigger()`**, and a view's
  history serializers are cached in `_history_serializer_classes` (was `_history_serializer_class`), one
  per `history_serializer_cache_key()`.
- `routed_actions()` leaves out a router's action whose method the viewset's `http_method_names`
  refuse - a 405 before it runs - so no coverage reports it.

### Fixed

- `HistoryMixin`'s `actor` filter follows the user model's pk: a UUID pk was compared as a number and
  every value refused.

## [0.29.1] - 2026-10-23

### Fixed

- **A proxy of a `DatabaseTimestampsModel` or a `VersionedModel` no longer fails to load.** isik
  registered the timestamp and version triggers on every concrete subclass, and a proxy is one - so it
  registered them a second time on the table the proxy shares with its concrete model, and pgtrigger
  refused the duplicate names (`Trigger name "protect_created_at" already used`). That broke any
  project proxy of a `BaseModel`, and every reverse relation in `concurrency_relations` to a `BaseModel`
  pointing at its owner, whose lock lives on a proxy of that model. A proxy now registers none: its
  table's triggers are its concrete model's.

## [0.29.0] - 2026-10-23

### Added

- **`concurrency_relations` covers a foreign key or one-to-one pointing at the model.** Name it by its
  accessor - `discount_set`, or its `related_name`. Its rows are the pointing model's own, every field of
  each, so adding, editing or deleting one moves the version, under every strategy. Rows pointing at a
  model never moved its `updated_at`, so `If-Match` on a write to them compared a version that couldn't
  change. Each gets the lock (or, for `"column"`, bump) trigger on its own table, from a proxy in the
  owner's app, registered whichever of the two models loads first. `assert_concurrency_field_changes()`
  takes a function making one such row for the instance it's given, creates it and deletes it. A reverse
  many-to-many isn't one: name it on the model that declares it.
- `UnguardedField`, `NoIdempotencyKey`, `NoReplay` and `RequestPolicyExemption` exemption types, and
  `RequestPolicy.exemption_type` for a policy to name a type of its own.

### Changed

- **Breaking: a reason is written as its exemption type, never a bare string.** At a call site a bare
  string reads as a value to be used - an exception message, say - rather than the reason a rule is
  skipped. Every opt-out isik takes now refuses one, naming the type to write:
  - `exempt_from_registry = ViewSetRegistryExemption(reason=...)` (or `SerializerRegistryExemption`);
  - `precondition_exempt_actions = {action: NoPrecondition(reason=...)}`, and
    `precondition_unread_actions` with `NoReadableVersion`;
  - `unguarded_fields = {field: UnguardedField(reason=...)}`, and `actions_writing_no_guarded_fields`
    with `WritesNoGuardedFields`;
  - `idempotency_exempt_actions` with `NoIdempotencyKey`, and `idempotency_no_replay_actions` with
    `NoReplay`;
  - each policy's `<policy>_exempt_actions` with `RequestPolicyExemption`, or the policy's own
    `exemption_type`.

  Each value of a map is now an exemption like any other: held to its type's 40-character floor (a map's
  reasons had none), listed by `manage.py exemptions`, and counted by budgets. A request policy's are
  listed under the policy, as before. A function or decorator whose call names the rule still takes its
  reason as text - `@writes_no_guarded_fields("...")`, `@no_database_form("...")`,
  `ConcurrencyToken(field, reason=...)`. A project overriding `actions_writing_no_guarded_fields` names
  `destroy` again, as its own `WritesNoGuardedFields`.
- **Breaking: `manage.py exemptions` fails when its listing is incomplete.** A call in the project's own
  apps that loading the project didn't run, or a type it didn't import, made only a line on stderr. The
  command now exits non-zero after listing, so a CI step running it is a gate.

## [0.28.0] - 2026-10-23

### Added

- **Every write that asks `If-Match` must have a read handing out its version - system check
  `isik.E004`.** `PATCH /users/me/` could require `If-Match` while `GET /users/me/` sent no `ETag`, and
  every check was green: a client had nothing to name. A write is now paired with its read within one
  urlconf, in order:
  1. the action `precondition_read_from = {write action: read action}` names, of the same view;
  2. a GET at the write's own path whose action is in `precondition_read_actions`;
  3. the same view's `retrieve`, for a `detail=True` write.

  The read must be of the same model. Exempt a write on purpose with `precondition_unread_actions =
  {write action: reason}`, each reason a `NoReadableVersion` exemption listed by `manage.py exemptions`.
  `precondition_read_problems()` gives the same list.

### Changed

- **Breaking: a read named in `precondition_read_actions` that answers without `get_object()` is
  refused.** It sent no `ETag`, and the static check can't see that. A GET or HEAD whose action is in
  `precondition_read_actions` and that succeeds without `get_object()` having run now raises
  `ImproperlyConfigured`, as a write that skipped the check does. A view that isn't a viewset is held to
  it only where its URL names one object, and a model without a concurrency token is left alone.

## [0.27.0] - 2026-10-22

### Added

- **Two more ways for a version to cover relations, by `concurrency_strategy`.** `"computed"` is
  0.26.0's, the default once `concurrency_relations` names anything.
  - `"column"`: each relation's table gets a `bump_<relation>_owner` trigger running a plain `UPDATE` of
    the owner row. The owner's own triggers then move its token - `version`, and `updated_at` with it -
    so the ETag stays the plain token, with no subquery to read. One owner `UPDATE` per row changed,
    never a loop: the owner's triggers only assign `NEW`.
  - `ConcurrencyParts(reason=...)`: the model's `concurrency_parts()` returns what the version covers,
    hashed with the token in Python (SHA-256). Its reason is an exemption listed by `manage.py
    exemptions`. Nothing proves the method covers everything, and what it reads is locked only for the
    relations named in `concurrency_relations`.
- `concurrency_strategy(model)`. `isik.E002` refuses an unknown strategy, `"computed"` or `"column"`
  naming no relations, and `ConcurrencyParts` without a `concurrency_parts()` method.

## [0.26.0] - 2026-10-22

### Added

- **A version can cover many-to-many relations.** `concurrency_relations = ("groups", "user_permissions")`
  on a model makes its ETag an MD5 computed in SQL from its token and each relation's through rows - the
  related ids, and a custom through model's own fields - so adding a user to a group is a change a stale
  `If-Match` is refused for, while `updated_at` keeps meaning "this row's own fields changed".
  `PreconditionMixin` computes it after taking the row lock. Each relation's table gets a generated
  trigger (`lock_<relation>_owner`, on a proxy model `<Owner><Relation>ConcurrencyLock` in the owner's
  app, so `makemigrations` writes it) that locks the owner row on every insert, update and delete there,
  so a membership change can't slip between the compare and the write. The same rows always give the
  same ETag. A model naming no relations is unchanged, ETag format included.
- `with_concurrency_etags(queryset)` reads each row's ETag in the list's own query, and
  `concurrency_etag_expression(model)` is the SQL. `concurrency_etag()` and
  `current_concurrency_etag()` return the computed ETag for such a model.
  `assert_concurrency_field_changes(instance, related={name: obj})` adds, removes, re-adds and clears each
  relation and asserts every step moves the ETag.
- `isik.E002` refuses a `concurrency_relations` name that isn't a many-to-many field of the model, and the
  database check `isik.W002` warns while a relation's lock trigger isn't installed.

## [0.25.0] - 2026-10-21

### Fixed

- **A 412 no longer fails when its `current` needs a query.** `PreconditionMixin` marked the
  transaction for rollback before rendering `current`, and a transaction marked for rollback refuses
  every query after it - so a `retrieve` serializer with a many=True relation, or any field that
  queries, turned every 412 into a `TransactionManagementError` and a 500. `current` and the `ETag`
  are now rendered first.
- **`ConcurrencyTokenField` no longer fails on a lazy object.** A serializer showing `request.user` - a
  `SimpleLazyObject` - looked the model up with `type()`, which is the proxy's own class, and raised
  `AttributeError`: a 500 on `GET /users/me/`. The token, the lock, the read-back and the test helper
  now read the class through the proxy.

### Changed

- **Breaking: a write that asks for `If-Match` and succeeds without `get_object()` is refused.** The
  check runs in `get_object()`, and an action needn't call it - a `detail=False` `PATCH /users/me/`
  that loads its row itself was listed as covered by `precondition_coverage()`, yet never compared
  `If-Match`. It now raises `ImproperlyConfigured` after the fact, and `ATOMIC_REQUESTS` rolls its
  writes back, as `GuardedFieldsMixin` does for a write that skipped its guards. Fetch the row with
  `self.get_object()`, or exempt the action. A replay under `IdempotencyMixin` is left alone.
- **`isik.E002` holds only a view that asks `If-Match` of some write to a token.** A view whose every
  write is exempt - over no versioned row at all - can use `PreconditionMixin` to declare its
  exemptions for `precondition_coverage()`. Such a view sends no `ETag` and ignores `If-None-Match` when
  its model has no token, and `AutoSchema` documents none.
- `has_concurrency_token(instance)` (`isik.django.apps.common.db.concurrency`).

## [0.24.0] - 2026-10-21

### Added

- **Optimistic concurrency: `PreconditionMixin`** (`isik.django.drf.viewsets`), RFC 9110's `ETag` /
  `If-Match`. A page loaded before someone else saved could send its write anyway, and the second write
  silently replaced the first. Now `retrieve` sends the row's version as a strong, opaque `ETag`
  (`If-None-Match` naming it gives 304), and PUT, PATCH, DELETE and a `detail=True` POST need
  `If-Match` - 428 without it, and 412 with a stale one:
  `{"detail", "code": "precondition_failed", "current"}`, `current` the row as `retrieve` shows it,
  with its current `ETag`. The comparison runs in `get_object()`, after authentication, permissions
  and the 404, with the row locked (`SELECT ... FOR UPDATE`) and read again, so two writes made
  against one version land one and refuse the other. It needs `ATOMIC_REQUESTS`. `If-Match: *` matches
  any current version, a list of tags matches if any does, and a `W/` prefix is ignored: the tag is
  the row's version, not one response's bytes, and compression weakens it on the way out. A
  successful write sends the version the database left, read back, and a create sends its new row's.
  `precondition_exempt_actions = {action: reason}` exempts an action; each reason is a
  `NoPrecondition` exemption. Under `IdempotencyMixin` a retry replays its first success instead of
  getting 412, in either order of the two mixins.
- **Five options for it**, each a setting that a view's own attribute overrides:
  `PRECONDITION_IGNORES_WEAK_TAGS` / `precondition_ignores_weak_tags` (default on; off is RFC 9110's
  strong comparison), `PRECONDITION_REQUIRED` (default on; off lets a write without `If-Match` through
  unchecked), `PRECONDITION_ALLOWS_ANY` (default on; off refuses `If-Match: *` with 428),
  `PRECONDITION_MISSING_ROW_STATUS` (404 by default, or the RFC's 412 for a write with `If-Match` to a
  row that isn't there), and `PRECONDITION_NOT_MODIFIED` (default on; off answers `If-None-Match` in
  full). The system check `isik.E003` refuses a value an option can't take, and `isik.W001` warns about
  refusing `*` while not requiring `If-Match`, which protects nothing.
- **Concurrency tokens** (`isik.django.apps.common.db.concurrency`). A model's token is its
  `concurrency_field`: `version` on a `VersionedModel`, else `updated_at` on a
  `DatabaseTimestampsModel`. Any other field is declared with
  `ConcurrencyToken(field, reason=...)`, since a trigger's SQL can't be proven to change it on every
  write. `assert_concurrency_field_changes(instance)` proves it does, on a real database, for a test.
- **`VersionedModel`**: a `version` starting at 1, set to `OLD.version + 1` by a BEFORE UPDATE trigger
  whatever the write set it to. Not editable, and like `updated_at` it doesn't count as a change in
  history.
- **`ConcurrencyTokenField`** (`isik.django.drf.serializers`): the same string as the `ETag`, in the
  body, so each item of a list carries the one to send back.
- **`precondition_coverage()`** (`isik.django.drf.coverage`): which routed writes to one object ask
  for `If-Match`, which are exempt and why, and which don't.
- **System check `isik.E002`**: every routed `PreconditionMixin` view's model has a token that can be
  trusted - not null, an integer, datetime or UUID, kept by one of isik's triggers or declared - and no
  serializer the view declares lets a request write it.
- **`AutoSchema`** documents a `PreconditionMixin` view's `If-Match` / `If-None-Match` headers, its
  `ETag` response header, and its 304, 412 and 428 responses, so generated clients know them.

### Changed

- **`updated_at` now strictly increases on every update.** The trigger stamps
  `GREATEST(clock_timestamp(), OLD.updated_at + interval '1 microsecond')` instead of
  `GREATEST(clock_timestamp(), OLD.updated_at)`. That could leave the value where it was - two writes
  in the same microsecond, or the server's clock stepping back - and then a write made against the old
  value would pass for one made against the new one. Every update now moves it on by at least a
  microsecond, timestamptz's resolution, so `updated_at` is a valid concurrency token on every
  `BaseModel` with no new column. After a clock step back it runs ahead of the clock, a microsecond
  per write, until the clock catches up. **A migration:** every `DatabaseTimestampsModel` - every
  `BaseModel` - gets its trigger replaced on the next `makemigrations`.

## [0.23.0] - 2026-10-15

### Fixed

- **`updated_at` no longer goes backwards.** The `stamp_updated_at` trigger stamped `NOW()`, which in
  Postgres is when the transaction began, not when the row was written. A transaction that began
  first and wrote last - a request under `ATOMIC_REQUESTS`, a long task - stamped the row earlier
  than the write before it, and the history showed that as an `updated_at` pair reading backwards.
  The trigger now stamps `GREATEST(clock_timestamp(), OLD.updated_at)`: the time of the write itself,
  never earlier than the value it replaces. **A migration:** every `DatabaseTimestampsModel` -
  every `BaseModel` - gets its trigger replaced on the next `makemigrations`.

### Changed

- **A save no longer lists a history update just for restamping `updated_at`.** The trigger bumps it
  on every update, so a resource showing `updated_at` listed every save as a change - each sign-in's
  `last_login` write, and a `save()` that changed nothing. A column the row keeps current itself -
  `updated_at` on a `DatabaseTimestampsModel`, any `auto_now` field - no longer decides whether an
  update is listed. It's still shown, and still in `changes` beside a real change; naming it in
  `shows_change_of` makes it count again.
- **`ignores_change_of=` / `HistoryMixin.history_ignores_change_of`** add fields of your own whose
  change alone doesn't list an update. A name that isn't tracked, or that `shows_change_of` also
  names, raises `ImproperlyConfigured`. `history_columns` is now the columns whose change lists an
  update, without the ignored ones.

## [0.22.0] - 2026-10-10

### Fixed

- **A history serializer over a feed of several tracked models no longer raises.** `generic_history_serializer()`
  builds for one model, and a queryset selecting from `pghistory.models.Events` across several hands
  it rows recorded for another. Reading one went straight at `pgh_data[column]`, so the first row
  from a different event model raised `KeyError` and the endpoint answered 500. Such a row now
  renders every one of this model's fields as `null` - all of them, not only the columns it happens
  to lack, because two tracked models usually share `id`, `created_at` and often a `name`, and those
  belong to a different object. The row's own `pgh_model` is what says which model recorded it, so a
  queryset of one concrete event model - which has no such column - reads exactly as before. A row
  recorded for *this* model whose snapshot is missing the column still raises: that is the snapshot
  and the field disagreeing about what is tracked.
- **The generated history serializer can be documented.** It is a plain `Serializer` carrying the
  resource serializer's own fields, and drf-spectacular types a read-only `PrimaryKeyRelatedField`
  from `field.parent.Meta.model`. With no `Meta` it documented every relation in a history as a bare
  string and warned while doing it - isik's own `AutoSchema` failing on isik's own serializer, and a
  project generating with `--fail-on-warn` could not generate at all. The class now carries
  `Meta.model` (the event model), which DRF itself ignores on a plain `Serializer`.


## [0.21.0] - 2026-10-09

### Changed

- **History shows what the resource shows, and nothing else.** `HistoryMixin` served every tracked
  column minus `history_withhold`, with nothing tying that list to the viewset's serializer, so a
  `User` viewset that hid `password` served the hash from `/users/{pk}/history/` the moment someone
  forgot the line. The history now follows the serializer `retrieve` uses: each of its fields that
  renders one tracked column appears under the same name and rendered by the same field, so an entry
  reads like the resource did at that moment. A column the serializer leaves out stays out, `changes`
  included, and an update that changed only such columns isn't listed. Method fields, nested
  serializers, dotted sources and non-primary-key relations are left out - they'd read today's rows.
- `history_shows_change_of = ["password"]` (`shows_change_of=` on `generic_history_serializer()`)
  keeps a hidden field's changes listed as `[None, None]`, as `history_withhold` did - the event says
  the password changed, never the hash. `history_source_serializer()` picks another serializer to
  follow. The history serializer renders with the viewset's context.

### Added

- `HistoryMixin.scope_history(events)` narrows the events both history endpoints return, per
  request - one override for rules about the events themselves, such as only those recorded while
  the caller owned the object (`events.filter(pgh_data__owner_id=request.user.pk)`) or only those
  the caller made. Until now that meant overriding `get_history_queryset()` and
  `get_all_history_queryset()` separately; the hook also covers either override.

### Fixed

- **A template field's loops have one budget per render.** `max_loop_iterations` capped each
  `range()` call on its own, and `max_render_length` only counted output, so three nested
  `range(1000)` loops that wrote nothing ran 300M iterations under `TemplatePolicy.STANDARD()`, and a
  fourth level ran for hours. Every `{% for %}` iteration of a render now counts against
  `max_loop_iterations`, including loops over literals and over the context. Each `range()` call is
  still capped as well.
- **`*` and `**` in a template can't build a huge value.** `{{ "x" * 400000000 }}` allocated 400MB
  before the output check saw it, and `{{ 9 ** (9 ** 9) }}` never finished. A `*` or `**` whose
  result would be longer than `max_render_length` is refused before it's computed.
- **A tag name can't be empty.** `get_tag("")` created a tag named `""`, because the default regex
  ended in `*` and Django skips validators for an empty value. The regex now needs at least one
  character, and `get_tag()` cleans the name through the field, blank check included. The message
  names `.`, which was always allowed, and is translatable.
- **An empty list variable is the empty list.** `comma_separated_list()` read `ALLOWED_HOSTS=` as
  `[""]`, and the int and float lists raised. All three now return `[]` for a value that is set but
  empty. An empty item between two commas is unchanged.
- **A config parse error doesn't print the value.** The `ConfigError` for an unparseable variable
  printed the raw value, so a malformed secret ended up in logs and Sentry. It names the variable and
  the caster (by name, not `<function ... at 0x...>`) instead. The caster's own error is still its
  `__cause__`.

### Removed

- **`UsernameOREmailModelBackend`.** Its `.get(Q(email=x) | Q(username=x))` found two users when one's
  username was another's email, or when two accounts shared an email, and the login was a 500 - so
  one signup could lock someone out of password login. Which lookups are safe depends on what a
  project keeps unique, so isik leaves the backend to the project, or to django-allauth's
  `AuthenticationBackend` (`ACCOUNT_LOGIN_METHODS = {"username", "email"}`). `login_through()` and
  `listed_backend_path()` stay.

### Breaking

- `history_withhold` and `generic_history_serializer(withhold=)` are gone, with no alias: a field
  is withheld by leaving it out of the serializer, and `history_shows_change_of` lists the ones
  whose changes should still show. `generic_history_serializer(model, serializer, ...)` takes the
  resource's serializer. History responses lose every column their serializer doesn't show, use the
  serializer's names and formats (`owner`, not `owner_id`, for a `ModelSerializer`'s foreign key),
  and leave out updates that changed nothing shown.
- `UsernameOREmailModelBackend` is gone, with no alias: a project listing it in
  `AUTHENTICATION_BACKENDS` needs allauth's backend or its own.
- `max_loop_iterations` is a total for the render, not a per-`range()` cap, so a template with two
  loops of 600 now fails under the default 1000. Raise the limit on the policy if one needs more.
- A `*` or `**` in a template whose result would be longer than `max_render_length` raises
  `TemplateSecurityError`, even when the value is never printed (`{% if "x" * 20000 %}`).
- Tag fields get a migration: the default validator's regex and message changed (no SQL).
- `get_tag("")` raises `ValidationError` ("This field cannot be blank.") instead of creating a tag
  named `""`.
- A list caster over a variable that is set but empty returns `[]`: `comma_separated_list()` used
  to give `[""]`, and the int and float lists used to raise, which an `error_default` would have
  caught.
- `ConfigError`'s parse message no longer has the value in it, so code matching on that text needs
  updating.

## [0.20.0] - 2026-10-08

### Fixed

- **isik's own exemptions no longer show up as the project's.** The reasons isik gives for Django's
  validators without a database form (regex, email, URL and the rest) are made from `ready()`, so
  they were recorded at the first frame outside the libraries - `manage.py` - and listed as the
  project's: seven entries under `isik.validators.database-form` on a fresh project, every one at
  `manage.py:15`, and a budget for that rule starting at seven. They are now recorded at the line in
  isik that makes them and left out by default.

### Added

- `@makes_own_exemptions` marks a library function whose exemptions are the library's own. Each is
  recorded at the function's own line with `library=True`. `--include-library` on
  `manage.py exemptions`, and `include_library=True` on `declared_exemptions()`,
  `project_exemptions()` and `project_exemption_rules()`, list them; JSON entries carry `"library"`.
- `lifted_constraint(model, name)`, for a test: lifts one CHECK for a block, from
  `_meta.constraints` and from the table, inside the test's own transaction, and puts it back
  afterwards (`NOT VALID` at the column, so the rows the block wrote stay until the rollback).
- `docs/django/apps/common/db/constraints.md` says what a callable `choices` does: its CHECK is a
  snapshot taken when the app starts. Changing the setting it reads needs a migration, and a test
  that overrides the setting has to lift the constraint.

### Breaking

- `declared_exemptions()` leaves out a library's own exemptions unless `include_library=True`, and
  `assert_exemption_budget()` never counts them. `ExemptionEntry` gains `library`.

## [0.19.0] - 2026-10-08

### Added

- **A validator says whether its rule can hold at the column.** One kept only in `full_clean()` is
  one a `QuerySet.update()`, a `bulk_create`, a data migration or a psql session writes straight
  past, so where the rule is expressible in SQL it now also becomes a `CheckConstraint`. Three
  answers and no fourth: `as_condition` on the class, `@no_database_form(reason)` for a rule no
  column could hold, and `python_only_validator(validator, reason=...)` for one instance that
  declines. See `docs/django/apps/common/db/constraints.md`.
- Translations for Django's `MinLengthValidator`, `MaxLengthValidator`, `MinValueValidator` and
  `MaxValueValidator`, attached to Django's own classes rather than subclassed - so nothing has to be
  imported to get them, and the validators Django builds for itself are answered too. Each returns
  nothing where the column carries the rule already: a `varchar(n)` refuses a longer value by itself,
  and `PositiveIntegerField`'s bounds are the `integer` type plus a CHECK Django writes.
- A field's `choices` becomes a CHECK as well. Django validates them in `full_clean()` and nowhere
  else, so the column took whatever it was handed.
- `isik.E001`, this library's first system check, naming every validator that has said nothing.

### Breaking

- **A hand-written validator that says nothing fails `isik.E001`.** Every project on `BaseModel`
  will name its own validators once, which is the point of the change. Django's auto-added ones are
  not affected - the check reads `_validators`, what a model actually passed.
- **New constraints appear in migrations.** A `makemigrations` after upgrading writes a `CHECK` for
  every `choices` field and for every validator with a database form. Existing rows are checked when
  it applies, so a column holding something outside its own choices will refuse to migrate - which is
  the bug the constraint exists to have caught.
- **A rule enforced at the column can no longer be skipped.** `skip_full_clean()` and
  `SkipFieldValidators` lift a validator; nothing lifts a `CheckConstraint`, and Postgres refuses
  `DEFERRABLE` on one. A rule that has to stay liftable belongs in `python_only_validator`.

## [0.18.0] - 2026-10-08

### Fixed

- django-lifecycle's `bypass_hooks_for(models)` now does something. `FullCleanOnSaveModel.save()`
  replaces `LifecycleModelMixin.save()` rather than extending it, and the bypass is checked inside
  the method that was replaced - so the context manager ran, suppressed nothing, and raised nothing
  to say so. It is now asked about on every save.

### Added

- `bypass_hooks_for` covers deletes as well as saves. `FullCleanOnSaveModel` overrides `delete()` to
  consult it; upstream's `LifecycleModelMixin.delete()` does not, so this goes further than
  django-lifecycle on purpose - the name says hooks, not saves, and a bypass that quietly covers
  half of them is the same trap as one that covers none.

Bypassing hooks still runs `full_clean()`, exactly as `save(_skip_hooks=True)` always has: whether a
row is valid is not a question about hooks, and `SKIP_FULL_CLEAN` remains the only thing that
answers it. Both opt-outs are kept; neither replaces the other.

## [0.17.0] - 2026-10-06

### Added

- `RoutedAction.callback` - the callable the urlconf routes to, so a decorator applied around
  `as_view()` in a urlconf (`csrf_exempt(...)`, `transaction.non_atomic_requests(...)`, a project's
  own marker) can be read off the entry without a second walk of the urlconf.

### Changed

- **Breaking:** `LazyPrimaryKeyRelatedField` takes `queryset_func=` by keyword only, like the rest of
  its options - DRF's related fields never took positional ones.

### Fixed

- `unseen_exemption_calls()` no longer reports the exemption a `@makes_exemption` helper builds
  inside its own body - what a maker makes is recorded at its caller, so that line never appeared
  among the recorded ones and was flagged on every run. Calls inside a maker are skipped; calls to
  it still count.
- `MediaWhiteNoiseMiddleware` takes the script prefix off `MEDIA_URL` only as a whole path segment:
  under a `/sub/` prefix, a `MEDIA_URL` of `/subway/media/` is no longer cut down to `way/media/`.

### Mutation testing

isik is now mutation-tested in full, and every mutant is killed.

- Decorated functions are mutated too: `scripts/mutation_run.py` runs mutmut with a patch
  (`scripts/mutmut_decorators.py`) that emits each mutant copy without the decorators - they stay on
  the dispatching trampoline, so each still runs exactly once - instead of skipping the function. A
  `@makes_exemption` helper keeps its mark on every copy; the exemptions docs show how.
- mutmut 3.8, which recurses into decorated classes.
- No `# pragma: no mutate` remains, and only migrations are left out (`do_not_mutate`). The two
  files that were excluded whole - `metaclasses.py` and `http_exceptions/exceptions.py` - are
  mutated: `HTTPExceptions`' transform hooks are module-level functions now, which mutmut can
  instrument before the class that calls them exists.
- Memoized functions start every test with an empty cache, so a mutant isn't answered from what the
  original computed.

## [0.16.0] - 2026-10-06

### Added

- `makes_exemption(Type)` (`isik.common.utils.exemptions`) - marks a project helper that makes
  exemptions for its caller (`@not_atomic("...")`). Each exemption it makes is recorded where the
  helper was called rather than inside it, and the unseen scan counts calls to it. Under other
  decorators it marks every function down the `__wrapped__` chain.
- `unimported_exemption_types(paths, exclude=TEST_CODE)` - exemption types declared in source whose
  module was never imported, so their rule and exemptions were missing from the listing. Types are
  read with `ast`: a class whose base is `Exemption` or another type (declared in any file, in any
  order), or an `X = exemption_class(...)`. `manage.py exemptions` reports them on stderr, and in JSON
  under `"unimported_types"`.
- `project_urlconfs(urlconf=None)` (`isik.django.apps.common.urlconfs`) - `ROOT_URLCONF`, then every
  host's urlconf when django-hosts is installed and `ROOT_HOSTCONF` is set, each once. Given urlconfs
  (one, or a list or tuple) are used as they are. isik doesn't depend on django-hosts.
- `RoutedAction.urlconf` - the urlconf that routes the entry, since under django-hosts one route can
  appear in several.
- `manage.py exemptions --urlconf` can be given more than once.

### Changed

- `unseen_exemption_calls()` follows `from ... import X as Y`. It also counts calls to types and
  makers that are only declared in the scanned files, not imported.
- `routed_views()`, `routed_actions()`, `request_policy_coverage()`, `idempotency_coverage()` and
  `manage.py exemptions` walk every urlconf the project serves by default (`project_urlconfs()`),
  not only `ROOT_URLCONF`. Without django-hosts that's `ROOT_URLCONF` alone, as before. `urlconf=` also
  takes a list or tuple.
- **Breaking:** `unseen_exemption_calls()` and `unseen_project_exemptions()` skip test code by
  default (`exclude=TEST_CODE`: `tests` directories, `test_*.py`, `*_test.py`, `conftest.py`). Tests
  make exemptions on purpose and never run while a project loads, so every one used to be flagged.
  Pass `exclude=()` to scan everything.
- **Breaking:** `manage.py exemptions --format json` names each `"unseen"` entry's callee `"name"`
  rather than `"type"`, since it can be a maker function as well as a type.

## [0.15.0] - 2026-10-05

### Added

- `Exemption` and `exemption_class()` (`isik.common.utils.exemptions`) - a `str` that skips one named
  rule and carries why, made with `reason=` and held to a floor (40 characters by default, after
  whitespace collapses). A type names its `rule` (a dotted slug, unique across the project) and its
  `why`, which every refusal repeats; `min_length` and `shows_as` (`""` for a `help_text`/`db_comment`
  sentinel) are optional. Equal to and hashed as what it shows as, copied and pickled as itself, and
  written into migrations as the call that made it.
- Listing exemptions by rule: `exemption_types()`, `declared_exemptions(rule=None)` (each records the
  file and line that made it), `assert_exemption_budget(rule, at_most=...)` for a test that makes
  adding one a reviewed change, and `unseen_exemption_calls(paths)` for calls that never ran.
  `manage.py exemptions` (`--rule`, `--rules`, `--format json`) lists a Django project's, with each
  routed `RequestPolicy`'s `{action: reason}` exemptions under `policy.<policy-name>`, and warns
  about exemption calls in the project's own apps that the import didn't run.
- `ViewSetRegistryExemption`, `SerializerRegistryExemption` and `WritesNoGuardedFields` - the types of
  isik's own single opt-outs, under the rules `isik.viewset-registry`, `isik.serializer-registry` and
  `isik.guarded-fields.writes-none`.
- `object_stream_index()` - `track_events()` now gives every event table with a `pgh_obj` a
  `(pgh_obj, -pgh_id)` index, so reading one object's history no longer costs the square of its
  length. `obj_field=None` tables get none; a caller's own `meta["indexes"]` stay beside it.
- `HistoryMixin.history_component_prefix` - goes in front of the generated history serializer's
  name and schema component, so a second `HistoryMixin` viewset over a model (`"Staff"` ->
  `StaffWidgetHistory`) no longer collides with the first. The default `""` changes no name.
- `routed_views()` (`isik.django.drf.coverage`) - every routed view, DRF's or not, each entry's
  `kind` a `ViewKind`: `DRF`, `CLASS` (a Django class-based view, one entry per method it implements)
  or `FUNCTION` (once, `method=None`). `request_policy_coverage(..., plain_views=...)` takes the
  project's own judgment of a view that isn't DRF's - `True`, `False` or a reason - and reports
  those views too. `RoutedAction` gained `kind`, defaulting to `ViewKind.DRF`.

### Changed

- **Breaking - removed, no aliases:** `DeclaredString`, `DeclaredText`, `DeclaredAttribute`, `text()`
  and `attribute()`, and the `isik.common.utils.declared_string` module. Their one use was building
  exemption sentinels, which `Exemption` now does:

  ```python
  # Before
  class NoHelpText(DeclaredString, displays_as=""):
      reason = text(min_length=40)

  help_text=NoHelpText("the labels of its choices already say what it holds")

  # After
  class NoHelpText(Exemption, rule="schema-docs.help-text", why="Every field should say what it holds in help_text.", shows_as=""):
      pass

  help_text=NoHelpText(reason="the labels of its choices already say what it holds")
  ```

  A reason is keyword-only, so a migration that wrote `NoHelpText('...')` needs regenerating (or
  editing to `NoHelpText(reason='...')`).
- **Breaking:** `exempt_from_registry` and `@writes_no_guarded_fields(...)` take a reason of at least
  40 characters, or an instance of their type, and refuse a shorter one when the class is defined.
  The attribute holds the typed exemption afterwards.
- **Breaking:** a `{name: reason}` map (`unguarded_fields`, `<policy>_exempt_actions`,
  `idempotency_exempt_actions`, ...) still has no floor, but refuses a placeholder (`n/a`, `tbd`,
  `none`, `x`, `-`, `.`, `?`) as it does a blank reason.

### Fixed

- `DatabaseTimestampsModel`'s `stamp_updated_at` trigger assigned to `updated_at` by its field name,
  so giving the field a `db_column` failed every UPDATE. It now stamps the quoted column. Every
  project's trigger SQL changes from `NEW.updated_at` to `NEW."updated_at"`, so `makemigrations`
  writes one migration replacing each model's trigger (and adding the new history indexes).

## [0.14.0] - 2026-10-04

### Added

- `Middleware` (`isik.django.apps.common.middleware`) - the `get_response` constructor every
  hand-written middleware opens with, and a `__call__` around `before(request)` (return a response
  to answer without the view) and `after(request, response)`. Django's other hooks stay Django's.
  `RequestContextMiddleware` and `ExceptionHandlerMiddleware` are now built on it.
- `NarrowingFilterMixin` (`isik.django.drf.filters`) - a django-filter filter that leaves the
  queryset alone when its value wasn't given and narrows it with `narrow(qs, value)` when it was, so
  `if value in EMPTY_VALUES: return qs` is written once. isik's own context-field history filter uses it.
- `login_through(request, user, backend)` and `listed_backend_path(backend)`
  (`isik.django.apps.common.backends`) - log in through a backend *class*, resolved to the path it's
  listed under in `AUTHENTICATION_BACKENDS` (a re-exporting package's path counts). An unlisted one
  raises `ImproperlyConfigured` where the login is written, instead of Django's silent logout on the
  next request.
- `Meta.create_only_changes = "refuse"` on `CreateOnlyFieldsMixin` - an update changing a
  create-only field is a 400 naming it (code `create_only`), while the same value sent back
  unchanged still passes. `"ignore"`, the default, keeps today's behavior.
- `SignedInPermission` (`isik.django.drf.permissions`) - refuses anyone not signed in, then asks
  `allows(user, request, view)`. `IsAuthenticatedANDSignupCompleted` is now one.
- `docs/naming.md` - the rules isik's names follow, and `tests/test_naming.py` failing on the parts a
  test can see: American spelling, acronyms in capitals, no truncated class or module names.
- The README and docs index point Django users at the `isik.common.utils` helpers they keep
  rewriting (`first_of`, `not_none`, `with_attrs`, `returns`, `DeclaredString`).

### Changed

- **Breaking - renamed, no aliases:**
  - `DeclaredStr` -> `DeclaredString`, `isik.common.utils.declared_str` ->
    `isik.common.utils.declared_string`.
  - `isik.django.apps.common.fields.gfk` -> `isik.django.apps.common.fields.generic_foreign_key`
    (`AutoGenericForeignKey` is still importable from `isik.django.apps.common.fields`).
  - `AutoGenericForeignKey.limit_gfk_models_to` -> `limit_content_types_to`.
- American spelling throughout: "honors" in the idempotency mixin's misconfiguration message, so its
  catalog entry moved with it.

## [0.13.1] - 2026-10-03

### Fixed

- `isik.__version__` said `0.1.0` in every release since 0.1.0. It is now where the version lives:
  `pyproject.toml` declares the version dynamic and hatchling reads it from `isik/__init__.py`, so the
  two can't disagree.

## [0.13.0] - 2026-10-03

### Added

- `RequestPolicy` + `RequestPoliciesMixin` (`isik.django.drf.viewsets`) - a rule about the request
  ("this organization is set up", "this account has its second factor") on any DRF view, not only one
  with a model surface. `request_policies = [...]` runs after the view's own permissions in
  `check_permissions()`, so a `get_permissions()` override can't drop one. A policy says
  `allows(request, view)`, its `message`/`code`, and can add to its refusal's response (`refused()`,
  for a header DRF can't render). Exemptions are `{action: reason}` under the policy's own attribute
  (`<policy_name>_exempt_actions`, or `exemptions_attribute`).
- `BaseViewSet` - the model-free half of `BaseModelViewSet`: `RequestPoliciesMixin`,
  `ActionSerializerClassMixin` and `GuardedFieldsMixin` on `GenericViewSet`, for a viewset over
  something that isn't one of your models. `BaseModelViewSet` is now built on it.
- `BaseSerializer` - the model-free half of `BaseModelSerializer` (`MetaCombiningMixin`,
  `RequestContextMixin`, `ConditionalSerializerMixin` on `Serializer`). `BaseModelSerializer` is now
  built on it.
- `UUIDPrimaryKeyModel`, `DatabaseTimestampsModel`, `FullCleanOnSaveModel`, `ReprModel`
  (`isik.django.apps.common.db`) - `BaseModel`'s four parts, each usable alone. `BaseModel` is now
  composed from them, with the same fields and behavior.
- `isik.django.drf.coverage`: `routed_actions()` walks a urlconf's DRF views, and
  `request_policy_coverage(policy)` reports each routed action as covered, exempt (with its reason)
  or uncovered - what a project's own check or test fails the build on.
  `idempotency_coverage()` (`isik.django.apps.idempotency.coverage`) does the same for idempotency
  keys.
- `DeclaredStr`, `text()`, `attribute()` (`isik.common.utils.declared_str`) - a `str` sentinel whose
  value, validation (lengths, a pattern) and carried attributes are declared on the class. The first
  `text()` is the positional argument; `displays_as=""` makes it empty to Django while it carries its
  reason. Survives `copy`/`pickle`, and `deconstruct()`s for migrations as itself. isik itself
  doesn't use it - it's for a project's own `Exemption`/`NoComment`/`NoHelpText`.
- Every model field isik declares or generates has a `help_text` (translatable) and a `db_comment`.

### Changed

- **Breaking: an opt-out isik can't check says why.** A reason is any non-blank string; a bare
  `True`, a list of names or a bare decorator raises `ImproperlyConfigured`.
  - `exempt_from_registry = "<why>"` on viewsets and serializers.
  - `unguarded_fields = {field: reason}` and `actions_writing_no_guarded_fields = {action: reason}`.
  - `@writes_no_guarded_fields("<why>")`.
- **Breaking: migrations regenerated.** The idempotency apps' `0001_initial` now carries every
  column's comment - regenerated in place rather than followed by a `0002`, so a project that
  migrated 0.12.0 gets no comments until it recreates those tables. Models built by isik's makers
  (`votes()`, `notes()`, `comments()`, `bookmarks()`, `tags()`) and `AutoGenericForeignKey`'s columns
  now carry `help_text`/`db_comment`, so `makemigrations` will want an `AlterField` for each.
- `BaseModelViewSet`'s mixin order: the model-free ones now come after the model-bound ones, as
  `BaseViewSet`.

### Fixed

- `HistoryContextTask` put the cause's raw values in the message headers, and a `UUID` user pk -
  pghistory's own value on any project with UUID primary keys - can't be encoded by AMQP:
  `FrameSyntaxError` on every task dispatched from an authenticated request, with the default
  settings, on RabbitMQ. The cause now travels through `header_safe()`: the JSON encoder pghistory
  stores its context with, so a value becomes what its row would hold anyway. Override
  `header_safe()` for values that encoder doesn't know.

## [0.12.0] - 2026-10-02

### Added

- Idempotency keys (`isik.django.apps.idempotency`). A caller sends `Idempotency-Key: <uuid>`, and a
  retry of the same request gets the first response back instead of the work being done twice.
  `IdempotencyMixin` (`isik.django.apps.idempotency.drf`) honors it on any DRF view. The claim is
  inserted inside the request's transaction, against a `UNIQUE (claimed_by, key)` index, so a
  concurrent retry waits for the first request and then replays it, or runs for real if it rolled
  back. There is no in-flight state, nothing to reap and no expiry. Needs `ATOMIC_REQUESTS` (or an
  equivalent transaction) and Postgres.
  - Two claim apps, install one: `idempotency.by_reference` (`IdempotencyClaim`) replays by
    re-serializing the row the response named and never stores a body;
    `idempotency.with_body` (`IdempotencyClaimWithBody`) keeps the body, through a pluggable codec
    (`IDEMPOTENCY_BODY_CODEC`) so it can be encrypted at rest. Each ships its own migration, so the
    body column only exists where it's used. Or subclass `AbstractBaseIdempotencyClaim` /
    `AbstractIdempotencyClaim` / `AbstractIdempotencyClaimWithBody` and set `IDEMPOTENCY_CLAIM_MODEL`.
  - Covers `POST` by default, with the key required. `{action: reason}` exemptions
    (`idempotency_exempt_actions`), and `idempotency_no_replay_actions` for responses shown once:
    those keep only their status and refuse a repeat with 409.
  - The fingerprint is sha256 over method, path, sorted query and the body as sorted-key JSON, so
    reordered JSON is the same request. An uploaded file is described by its content hash rather than
    the multipart bytes, whose boundary changes on every retry. What the body is compared by can be
    replaced globally (`IDEMPOTENCY_NORMALIZE`), per view or per `@action`.
  - Answers: 400 for a missing or malformed key, 422 for a key reused on a different request, 409 for
    a no-replay action, 410 for a replay whose row is gone, `Idempotent-Replayed: true` on a replay.
  - A raised failure rolls its claim back. A returned 4xx releases its claim. Either way the key isn't
    spent.
  - `IDEMPOTENCY_LOCK_TIMEOUT` (milliseconds, off by default) bounds how long a retry waits on the
    first request, answering 409 `idempotency_key_in_flight` when it runs out.

## [0.11.0] - 2026-09-27

### Added

- `HistoryContextMiddleware` (`isik.django.apps.common.middleware`) - pghistory's `HistoryMiddleware`,
  plus `open_history_context()` (`isik.django.apps.common.db`) to read the context it opened while
  the request is served. pghistory publishes no reader of its own. It reads the live context, so a
  user DRF authenticates in the view is included, which a copy taken in `get_context()` would miss.
- `HistoryContextTask` (`isik.django.celery`, new `celery` extra) - a Celery task base whose history
  rows name whoever caused the task. The request's cause travels in the message headers and is
  re-opened around the task body in the worker, with the task's name added. `carried_history_keys`
  picks which keys travel (`user` by default, never the `url`). A task nobody asked for records
  `caused_by: "system"` rather than an empty context. A caller's own headers are kept. A task run
  inside a request (eagerly, or called directly) leaves that request's context untouched.

## [0.10.0] - 2026-09-25

### Added

- `django_permission(permission, message=None, name=None)` (`isik.django.drf.permissions`) - a
  permission that passes only if the caller holds a Django permission (`request.user.has_perm`). Takes
  a permission name, a `str` subclass, a StrEnum/TextChoices member, or an Enum member whose value is
  one, and passes it to the backends untouched. Request-level only: `ModelBackend` answers `False` to
  every object-level check. An unauthenticated request is refused without asking the backend. Named
  after the permission (`HasInvitationsIssuePublicInvitation`). `message=` takes a template with an
  optional `%(permission)s` placeholder, a fixed message, or a callable asked on refusal as
  `message(permission=, request=, view=)`. By default it names the permission.
- Every user-facing string isik produces is translatable - permission messages, validation errors,
  HTTP exception descriptions (including stdlib's `HTTPStatus` ones in the middleware's response), and
  every raised exception, configuration errors included. With Django they come from Django's own
  catalogs. Without Django, from stdlib gettext's `isik` domain. Errors raised at import time fall back
  to the untranslated text instead of failing with `AppRegistryNotReady`. `isik/locale/isik.pot` lists
  every message for `msgmerge`. See `docs/translations.md`.

### Changed

- The `drf` extra now brings the `django` extra with it. `isik.django.drf` builds on `isik.django.apps`
  (`BaseModel`, history tracking), so `pip install isik[drf]` on its own used to leave imports failing.
- `README.md` is its own page instead of a symlink to `docs/INDEX.md`, whose `docs/`-relative links
  broke from the repo root on GitHub and on PyPI. It links to the docs with absolute URLs.

### Fixed

- `prevent_actions`, `only_actions`, `user_property` and `object_property` built their messages with
  an f-string inside `gettext_lazy`, so the msgid changed with every value and could never be
  translated. They use placeholders now; the rendered English is unchanged.
- `SkippableValidatorsMixin` (and so every `BaseModel`) connected one `class_prepared` receiver per model
  class with `sender=cls`. Django keys those by `id(cls)` and never removes them, so once a model class
  was freed (one defined in a test, or under `isolate_apps`), the next class allocated at the same
  address inherited the receiver and had its validators wrapped without mixing it in - an intermittent
  failure, and one leaked receiver per model. It's now a single receiver that checks what it's handed.
- `BaseModel`'s `id`/`created_at`/`updated_at` `verbose_name`s were translated once, at import, in the
  default language. They're lazy now, so they follow the active language. No migration.

## [0.9.0] - 2026-09-25

### Changed

- **Breaking:** `guarding()`'s value-targeted form is now its own keyword, `setting=`:
  `guarding(is_owner("issued_by"), setting={"names_issuer": True})`. `fields=` takes field names only,
  and a dict passed to it raises `ValueError` pointing at `setting=`. As `fields={...}`, it read as
  "names_issuer: guarded, yes" rather than "guarded when set to True".
- `is_owner(owner_field, of=None)` - both halves take a dotted path or a callable, so an owner can be
  relations away (`is_owner("installation.organization")`) or computed (`is_owner(lambda obj: ...)`).
  A path broken by a missing attribute or a `None` relation means "not the owner". A dotted
  `owner_field` used to be read as one attribute name and so always refused.
- `user_property`/`object_property` take the model's own descriptor, or a name, as the first
  argument: `user_property(User.is_staff)`, `user_property(User.quota)`, `user_property("is_app")`.
  Properties, model fields, forward and reverse relations, and both `functools`' and Django's
  `cached_property` all resolve to the attribute they read, which is then read off the instance, so
  a `cached_property` keeps its cache (a `property_=` used to be called through its raw `fget`). A
  reverse relation resolves to its accessor, not to the field on the other model. Anything with no
  name to find, like a plain class attribute's value, raises `TypeError`. `property_=`/`attribute=`
  still work.
- **Breaking:** generated permission classes are named like classes, not like the call that made
  them: `is_owner("issued_by")` is `IsOwnerByIssuedBy` (was `IsOwnerPermission(owner_field=issued_by)`),
  `is_owner("tenant", of="organization")` is `IsOwnerByTenantOfOrganization`, `user_property("is_verified")`
  is `UserIsVerified`, `object_property("is_app")` is `ObjectIsApp`, `prevent_actions("create", "destroy")`
  is `PreventCreateAndDestroy`, and `guarding(~object_property("is_app"), actions=["destroy"])` is
  `NotObjectIsAppForDestroy` (compositions are named too - `AOrB`, `AAndB`, `NotA` - where they used
  to show up as `SingleOperandHolder`). Nothing reads these names but people and error messages.

### Fixed

- A field guard no longer raises `ImproperlyConfigured` (a 500) when the request can't write the
  guarded field. That happened when `?only=`/`?exclude=` dropped it (a client could trigger it) or when
  the action's serializer from `serializer_class_action_map` doesn't carry it. Such a field is skipped
  now. A name that none of the viewset's serializers has still raises, as a typo.

### Added

- `name=` on every factory that builds a class for its caller: `is_owner`, `user_property`,
  `object_property`, `prevent_actions`, `guarding`, `generic_vote_serializer`/`generic_comment_serializer`/
  `generic_note_serializer`/`generic_bookmark_serializer`, `generic_tag_serializer`,
  `generic_history_serializer` and `FakeErrorSerializer`. Always optional; the defaults are unchanged
  apart from the permission names above.
- `model_name=` on `votes()`/`comments()`/`notes()`/`bookmarks()`, and `tag_model_name=`/
  `through_model_name=` on `tags()`, to name the generated models. Defaults unchanged, so existing
  tables and migrations are untouched.
- `guarding.values(*values)` - a `setting=` target matching any of several values:
  `setting={"status": guarding.values(Status.APPROVED, Status.FEATURED)}`. Every target is normalized
  to a `GuardTarget` when the guard is built (`ANY_VALUE` for `fields=`, `EqualsTarget` for a plain
  value, `OneOfTarget` for `guarding.values`); subclass `GuardTarget` for a custom one.
- Public building blocks, usable on their own: `guarding_values` (what `guarding.values` is),
  `GuardTarget`/`EqualsTarget`/`OneOfTarget`/`AnyValueTarget`, `evaluate_permission` (a permission
  tree as one predicate, three-valued without an object), `descriptor_attribute_name`, and
  `permission_name` (a class-style name for any permission entry, compositions included).
- Field guards can't be skipped by overriding a handler. `FieldGuardsOnSaveMixin` (part of
  `BaseModelSerializer`) runs the current viewset's field guards inside `save()`, before the write,
  however the serializer was built, and counts `save(**kwargs)` values as part of the write. Any
  other unsafe request that succeeds without its guards having run (a plain serializer saved by
  hand, an ORM write in a custom action) raises `ImproperlyConfigured` inside `transaction.atomic()`
  on every database (`field_guard_databases` narrows it), so its database writes roll back.
  `@writes_no_guarded_fields` marks an action that writes no guarded field. `destroy` is exempt via
  `actions_writing_no_guarded_fields`. A request DRF answers from an exception (a refused guard, a
  failed validation) rolls back whatever the handler wrote before it, `on_commit` callbacks
  included. The save hook only checks serializers for the viewset's own model.
  `check_guarded_fields()` now also takes `save_kwargs` and checks `many=True` writes row by row.
- `only_actions(*actions, name=None)` - the complement of `prevent_actions`: allows only the given
  actions (`OnlyListAndRetrieve`). Refuses to be built with none.
- `guarding.other_than(*values)` and `guarding.matching(predicate)` - `setting=` targets for "anything
  but these" and "whatever this callable accepts" (`NotOneOfTarget`/`MatchingTarget`, also public as
  `guarding_other_than`/`guarding_matching`).
- Shared-serializer check: when two `GuardedFieldsMixin` viewsets share a serializer and only one guards
  a field, defining the second raises `ImproperlyConfigured` naming both. A viewset that leaves the
  field open on purpose lists it in the new `unguarded_fields` attribute.
- `words_to_pascal(text)` (`isik.common.utils`) - PascalCase from any run of words, splitting on every
  non-alphanumeric character and keeping inner capitals, so already-PascalCase names survive
  (`snake_to_pascal("IsSuperUser")` gives `"Issuperuser"`).

## [0.8.0] - 2026-09-25

### Added

- `guarding(predicate, fields=... | actions=..., message=None)` (`isik.django.drf.permissions`) - narrows
  any permission, `&`/`|`/`~` compositions included, to some actions or some fields. `actions=` refuses
  with a 403 where DRF runs permissions. `fields=` refuses a write that actually changes a guarded field
  with a `ValidationError` keyed by that field, checked after validation against the typed value
  (a field echoed back unchanged isn't a change). `fields=` also takes a dict of target values, to
  guard only one direction of a toggle. The predicate is evaluated as one expression over request and
  object, so `~` works on object-level permissions (it refuses everything under DRF's own `NOT`).
  Without an object, object-only parts are unknown and an unknown answer allows. A guard must be a
  top-level `permission_classes` entry: composing one raises `TypeError`/`ImproperlyConfigured`,
  and a `fields=` guard on a viewset that doesn't run field guards raises `ImproperlyConfigured` rather
  than silently permitting everything.
- `GuardedFieldsMixin` (`isik.django.drf.viewsets`) - runs `fields=` guards from `perform_create`/
  `perform_update`, or explicitly via `check_guarded_fields(serializer)`. Composed into `BaseModelViewSet`.
- `object_property(property_=None, attribute=None)` - the `user_property` counterpart for the object
  being acted on, e.g. `guarding(~object_property(attribute="is_app"), actions=["partial_update"])`.
- `is_owner(owner_field, of=None)` - `of` compares `obj.<owner_field>` to an attribute of the user
  (dotted paths allowed) instead of the user itself, for rows owned by a tenant rather than a person.
  The generated class name only changes when `of` is given.

## [0.7.0] - 2026-09-10

### Added

- `DeclaredOrderingMixin`/`DeclaredOrderingFilter` (`isik.django.drf.viewsets.ordering`) - `declared_ordering`
  maps a `?ordering=` key exposed to API clients to the real field(s) (or ordering expression) it
  orders by, for a value that isn't itself a field - a stand-in for several columns, or one that
  shouldn't leak the real column name. Mirrors `FilterSetMixin`'s `declared_filters`: a key's `-`
  counterpart is always valid too and flips the sign of every field it maps to, and
  `DeclaredOrderingFilter` must be present in `filter_backends` (a drop-in `OrderingFilter`
  replacement) or this fails loudly at class-definition time. Composed into `BaseModelViewSet`
  alongside `ReverseOrderingMixin`.

## [0.6.0] - 2026-08-30

### Added

- `validate_inputs(*validators, **kwarg_validators)` (`isik.common.utils`) - decorator validating a
  function's own arguments where the function is declared, before its body runs. Positional
  validators line up with the function's positional parameters left to right (`self`/`cls` skipped
  by name); keyword validators name a parameter directly. A validator is any callable: return falsy
  to fail with a plain `ValueError` naming the argument and its value, or raise your own exception
  to propagate it unchanged instead - no single validator "protocol" required.

## [0.5.5] - 2026-08-30

### Fixed

- `isik.django.drf.spectacular.AutoSchema` no longer advertises the tracked model's own
  `filter_backends`-derived filters (e.g. `is_active`, `created_at__gte`) on `HistoryMixin`'s
  `history()`/`history_list()` routes - passing them there did nothing, since neither action calls
  `filter_queryset()`.
- `include=` is now omitted entirely from a `ConditionalSerializerMixin` serializer's documented
  parameters when it declares no `Meta.relational_fields`, instead of being published with no enum
  (effectively free-text, accepting a value that does nothing).
- A view whose `get_serializer_class()` raises (e.g. `GenericAPIView`'s own default, when
  `serializer_class` was never set) no longer fails `AutoSchema`'s entire document - caught the same
  way drf-spectacular's own equivalent internal call already is.

## [0.5.4] - 2026-08-30

### Added

- `drf-spectacular` is now part of the `drf` extra (`isik[drf]`) - `isik.django.drf.spectacular.AutoSchema`
  wires it project-wide, fixing schema gaps that are properties of isik's own mixins:
  - `HistoryMixin`'s `history()`/`history_list()` are now typed as returning a paginated array, get
    distinct operation ids instead of colliding (they tokenize identically once the path parameter
    is dropped), and their built-in filters appear as query parameters (`history_filterset_class`
    is applied inside the action rather than exposed as `filterset_class`, so drf-spectacular's own
    introspection never saw it otherwise).
  - Any serializer using `ConditionalSerializerMixin` gets `only=`/`exclude=`/`include=` documented
    as query parameters - read straight off the query string, so there was nothing for schema
    introspection to find on its own before. `include=`'s enum comes from `Meta.relational_fields`.

## [0.5.3] - 2026-08-30

### Added

- `context_field_filter(event_model, name)` (`isik.django.drf.viewsets`) - filters `HistoryMixin`'s
  history endpoints on a `ContextField`'s own real, indexed column instead of a `pgh_context` JSON
  lookup (`context_filter()`). `HistoryMixin`'s built-in `actor` filter uses it automatically
  whenever the tracked model has an `actor` `ContextField`, and unlike the JSON-based fallback,
  needs no `pghistory.middleware.HistoryMiddleware` installed.

### Fixed

- `HistoryMixin.check_permissions()` now enforces `history_list_permission_classes` directly
  (moved off `get_permissions()`, which a subclass is far more likely to override wholesale for
  unrelated reasons and silently drop the check by doing so).
- `HistoryMixin.history()`'s docstring no longer leaks its own implementation rationale into the
  public API description a schema generator publishes - trimmed to the one sentence a caller needs.

## [0.5.2] - 2026-08-30

### Added

- `generic_history_serializer(model, withhold=[...])` (`isik.django.drf.serializers`) - keeps a
  named tracked field out of the flattened output entirely, while `changes` still records that it
  changed at that event with its `[old, new]` pair nulled instead of the field's real values.
  Different from `track_events(exclude=[...])`, which drops a field from the event table itself -
  this is about API exposure, not retention. Explicit, one field name at a time; isik never
  guesses at what "looks sensitive". `HistoryMixin.history_withhold` forwards into it.
- `HistoryMixin.history_list_scoped_to_queryset` (`isik.django.drf.viewsets`, default `False`) -
  restricts `GET <endpoint>/history/` to events for objects `self.get_queryset()` would return,
  for a viewset whose `get_queryset()` is itself a security boundary rather than a convenience.

### Fixed

- `HistoryMixin.history()` hardcoded a `pk=None` parameter, so a viewset with a custom
  `lookup_field` (e.g. `lookup_field = "schema_name"`) raised `TypeError` before reaching a line
  of it - a 500, not a 4xx. Now takes `*args, **kwargs` like any other DRF detail action.
  `history()`/`history_list()` also gained their own docstrings, instead of falling back to the
  viewset's when a schema generator asks for one.
- `HistoryMixin`'s auto-built `FilterSet` silently ignored a value that failed a declared filter's
  own validation (e.g. `?created_after=not-a-date`, or `?actor=<uuid>` against the built-in
  integer-typed filter) and just answered as if unfiltered - django-filter's own default behavior.
  It now raises a 400 naming the rejected value instead. `context_filter()`'s own docstring points
  at `filter_cls=` for a project whose actor pks aren't integers, rather than isik guessing a type.

## [0.5.1] - 2026-08-30

### Fixed

- A `ContextField` named `"actor"` (`isik.django.apps.common.db`) - a `ForeignKey` one, producing
  a real `actor_id` column - collided with `generic_history_serializer()`'s own reserved
  `actor_id` name and raised `ImproperlyConfigured`, with no way to resolve it (`track_events(
  exclude=[...])` only excludes tracked *model* fields, not context fields). The real column now
  wins instead: `generic_history_serializer()`/`HistoryMixin` serialize `actor_id` from it,
  typed and indexed, and skip the `pgh_context` JSON annotation they'd otherwise fall back to.
- `generic_history_serializer()`'s `changes` (`isik.django.drf.serializers`) could include a
  `ContextField` column (e.g. `actor_id`) as though it were a change to the tracked object -
  pghistory diffs every non-`pgh_`-prefixed column on the event row generically, so a handoff
  between two actors with no real field edit reported `{"actor_id": [alice.pk, bob.pk]}`. Context
  field keys are now filtered out of `changes` - they record who acted, not what changed.

## [0.5.0] - 2026-08-29

### Added

- `ContextField`/`track_events(context_fields=[...])` (`isik.django.apps.common.db`) - real,
  indexed columns on a `@track_events()` event model, stamped from `pghistory.context()`/
  `HistoryMiddleware`'s own request-scoped context via one combined `BEFORE INSERT` trigger,
  instead of a `pgh_context__<key>` JSON lookup done at query time. Composes with
  `track_events(meta={"indexes": [...]})` for a composite index across multiple context fields -
  nothing isik-specific, `meta=` and `context_fields=` both just feed the same
  `pghistory.track()`/`create_event_model()` call.

### Changed

- `BaseModel.created_at`/`updated_at` (`isik.django.apps.common.db`) are now maintained at the
  database level instead of Django's `auto_now_add`/`auto_now`, which only fire from
  `Model.save()` and left `updated_at` silently stale after `QuerySet.update()`/`bulk_update()`/
  raw SQL. `created_at` gets `db_default=Now()` plus a trigger refusing any UPDATE that changes
  it; `updated_at` is stamped by a `BEFORE UPDATE` trigger on every UPDATE regardless of how it
  was issued. **Breaking**: requires `pgtrigger` in `INSTALLED_APPS` (already installs as
  `django-pghistory`'s dependency) - `BaseModel` raises `ImproperlyConfigured` at import time if
  it's missing. No migration path is provided for existing rows; a project adopting this takes
  its own migration.

## [0.4.1] - 2026-08-10

### Fixed

- `context_filter()`'s default filter (`isik.django.drf.viewsets`): `NumberFilter` cleans into a
  `Decimal`, which psycopg's JSON parameter adapter can't serialize when comparing against a
  pghistory context key transform - now defaults to an `IntegerField`-based filter instead.

### Changed

- Removed dead code exposed by mutation testing: `orm.starts_with()`'s redundant `output_field`
  (`Case` already infers it), `AutoGenericForeignKey`'s no-op `self.name = name` and `db_index=True`
  kwarg (both always overwritten/defaulted downstream), `CreateOnlyFieldsMixin`'s no-op
  `required=False` (DRF's own `include_extra_kwargs()` strips it once `read_only` is set), and the
  `target_name`/`target_related_name` defaults duplicated on `tags()`/`notes()`/`votes()`/
  `bookmarks()`/`comments()`'s internal `_XxxField.__init__` classes (now required kwargs, since
  their public makers always forward explicit values).
- Closed out the mutation-testing gate added in 0.4.0 from 654 surviving mutants to zero: real
  tests added wherever a mutation was actually observable (field-forwarding methods on the
  votes/bookmarks/notes/comments mixins, unique-constraint/constraint-name correctness asserted
  against live model meta instead of pre-migrated DB schema, exact error-message assertions,
  kwargs-forwarding), everything else marked `# pragma: no mutate` only once proven equivalent.

## [0.4.0] - 2026-08-10

### Added

- `event_model_for`/`history_middleware_installed` (`isik.django.apps.common.db`) - resolve the
  Event model django-pghistory generated for a `@track_events()`-tracked model, and detect
  whether `pghistory.middleware.HistoryMiddleware` is installed.
- `generic_history_serializer` (`isik.django.drf.serializers`) - a read-only serializer over a
  `@track_events()`-tracked model's history: `event_id`/`event_created_at`/`action`, a SQL-computed
  `changes` diff against the previous event of the same object, and every tracked field flattened
  at the top level, typed to match the real model field.
- `HistoryMixin`/`context_filter` (`isik.django.drf.viewsets`) - adds two paginated actions to a
  `BaseModelViewSet` for a `@track_events()`-tracked model: `GET <endpoint>/{pk}/history/` (one
  object, governed by the viewset's own permissions) and `GET <endpoint>/history/` (every
  instance, restricted to superusers via `history_list_permission_classes`). Both filterable on
  `action`/`created_after`/`created_before`/`object_id`/`actor` out of the box and extensible via
  `extra_history_filters`.
- Mutation testing via `mutmut` across `isik/`, run in CI (`.github/workflows/mutation.yml`) on
  every push/PR to `master` and gated on zero surviving mutants; genuinely equivalent mutants are
  marked `# pragma: no mutate` instead of chased with a test.

### Fixed

- `FlattenedOneToOneMixin`: a Django `ValidationError` raised by a model-level validator/`clean()`
  on the related object (only reachable via `full_clean()` inside its own `save()`, invisible to
  DRF's automatic per-field validation) is now translated into a DRF `ValidationError` via
  `django_to_drf_validation_error`, instead of surfacing as an unhandled 500.
- `ViewSetRegistryMixin`/`ModelSerializerRegistryMixin`: a class redefining itself under the same
  `__module__`/`__qualname__` (e.g. the same test rerunning in one process, or dev-server
  autoreload) no longer raises `ImproperlyConfigured` - only a genuinely different class claiming
  an already-registered model is treated as a conflict.

## [0.3.0] - 2026-08-03

### Added

- `config.ref()` (and `ref()`/`Ref` in `isik.common.config`) lets a caster's
  `missing_default` or `error_default` fall back to another setting in the same schema -
  e.g. `config.ref("PAGE_SIZE")` or `config.ref(dot="DRF.PAGE_SIZE")` - instead of only a
  static value. Referenced settings are resolved through their own caster/environment
  variable and can chain through further refs; unknown targets, nested-config targets, and
  ref cycles raise `ConfigError`.
- `is_base_class = True` on `RequiredAttributesMixin`, `ViewSetRegistryMixin`, and
  `ModelSerializerRegistryMixin` marks a class as a new base rather than a leaf: it's
  exempted from the required-attributes check without redeclaring `required_attributes`,
  and the two registry mixins fork a private `model_map` for that branch - so a project can
  build several independent `BaseModelViewSet`/`BaseModelSerializer` hierarchies (e.g. one
  per API) without them fighting over one global registry.
- `WriteOnlyFieldsMixin` (`isik.django.drf.serializers`) - `Meta.write_only_fields` marks
  fields settable but never serialized, the `create_only_fields` counterpart for secrets.
  Composed into `BaseModelSerializer`. A field in both `write_only_fields` and
  `create_only_fields` raises `ImproperlyConfigured` at class-definition time instead of an
  opaque assertion error on the first update request.
- `none_during_schema_generation` (`isik.django.drf.viewsets`) - decorator for a
  `get_queryset()` override that returns `self.model.objects.none()` during
  drf-spectacular/drf-yasg schema generation instead of running against
  `self.request.user` (which is `AnonymousUser` at that point).
- `FlattenedOneToOneMixin` (`isik.django.drf.serializers`) - `Meta.flattened_one_to_one_fields`
  exposes a reverse one-to-one relation's fields as if they were declared directly on the
  parent serializer, read and write-through (creates the related row on write if missing,
  updates it in place otherwise, both in one atomic transaction with the parent). Composed
  into `BaseModelSerializer`.
- Documentation across the DRF, feedback, tags, and templated_fields modules, plus a note on
  `BaseModel` about a `django_lifecycle`/`classproperty` interaction that can cause infinite
  recursion.

### Fixed

- `isik.django.drf.pagination.PageNumberPagination` now caps `page_size` at 1000 by
  default, overridable via a `DRF_PAGINATION_MAX_PAGE_SIZE` Django setting or by setting
  `max_page_size` on a subclass - previously a client could request an unbounded page size.
- `ContextLocal._get_var`: lock the create-if-missing branch, closing a race where
  concurrent first access to the same key could orphan a `ContextVar`.
- `user_property`: deny instead of crashing when `request.user` lacks the checked
  attribute/property (e.g. `AnonymousUser`).
- `UsernameOREmailModelBackend`: return `None` instead of crashing with
  `MultipleObjectsReturned` on an empty/`None` username.
- `serializer_method_include`: pass `None` through instead of crashing on `_path_override`
  when the wrapped getter returns a nullable relation.
- `BaseModel`: snapshot field values before lifecycle hooks run and widen `update_fields`
  with anything a hook (or `full_clean`) mutates, so hook-driven changes to fields outside
  `update()`'s own kwargs are no longer silently dropped from the `UPDATE`.
- `FakeErrorSerializer`: resolve fields by instantiating the source serializer instead of
  reading `Meta.fields` directly - now works with `Meta.exclude`, `Meta.fields = "__all__"`,
  and plain serializers with no `Meta` at all.
- `FilterSetMixin.filterset_class`: cache the built class per subclass instead of rebuilding
  it on every access.
- `purge_iterable`: build the exclusion set once instead of once per iteration.
- `enabled_if`: wrap the original function with `@wraps` in the disabled branch instead of
  returning a placeholder disconnected from it entirely.
- `CookieORHeaderSessionMiddleware`: raise `ImproperlyConfigured` at middleware init if
  `SESSION_HEADER_NAME` is missing, instead of crashing on request.
- `IsAuthenticatedANDSignupCompleted`: raise `ImproperlyConfigured` instead of a bare
  `AttributeError` when the user model never defines `SIGNUP_COMPLETED_FIELD`.

## [0.2.0] - 2026-07-24

### Added

- `feedback` app: `votes()`/`bookmarks()`/`notes()`/`comments()` model makers attach a
  per-host interaction model via `contribute_to_class()`, no migration to hand-write; each
  ships a `UserXMixin` (`user.upvote(post)`, `user.add_note(post, "...")`) and a
  `generic_x_serializer()`/`generic_x_field()` DRF helper.
- `tags` app: `tags()` attaches a per-host `Tag` pool plus an M2M through-table, deduped by
  name; `add_tag()`/`remove_tag()`/`set_tags()`/`tag_names()`, a
  `generic_tag_field()`/`generic_tag_serializer()` DRF pair, and name validators enforced
  through the shared `get_tag()` choke point so no write path can slip an invalid name
  through.
- `templated_fields` app: `TemplateCharField`/`TemplateTextField` store a Jinja template
  rendered on demand against a caller-supplied context; sandboxed via Jinja's
  `SandboxedEnvironment` plus an AST-based `TemplatePolicy` allowlist
  (loops/conditionals/macros/filters/tests) with resource limits; DRF integration renders
  `{"raw", "rendered"}` automatically and adds a live-preview viewset action.
- `isik.django.drf` gains `serializers/`, `viewsets/`, `utils/` packages and `schema.py`:
  model/viewset registries, conditional include/only/exclude, create-only fields, a
  Meta-combining serializer mixin, current-user default, lazy relations, a related-count
  field, and filterset/ordering/protected-destroy viewset mixins.

### Changed

- `common` app restructured into `admin/`, `db/`, `email/`, `skippable_validators/`
  packages, plus shared `_model_makers.py` plumbing used by both `feedback` and `tags`.

### Fixed

- `parse_and_check()` only catches exceptions prosemirror-py actually raises
  (`ValueError`/`AssertionError`), not Django's `ValidationError`.

## [0.1.0] - 2026-07-12

Initial release: `isik`, a personal toolkit of everyday Python/Django/DRF utilities.

### Added

- `isik.common.utils`: `noop`, `identity`, `with_attrs`, `returns`, `raises`,
  `camel_to_snake`, `snake_to_pascal`, `snake_to_human`, `require_exclusive_keys`,
  `not_none`, `first_of`, `cloned`, `Sentinel`, `purge_iterable`, `purge_mapping`,
  `all_combinations`, `TransformExceptions`, `SuppressAndRun`, `suppress_callable`,
  `ThreadLocal`, `ThreadLock`, `ContextLocal`, `get_cached`, `enabled_if`.
- `isik.django.apps.common`: `BaseAdmin` (DALF + django-object-actions base admin) with an
  `action()` decorator; `UsernameOREmailModelBackend` auth backend; `AutoGenericForeignKey`;
  `MediaWhiteNoiseMiddleware`; `CookieORHeaderSessionMiddleware`; `BaseModel` (skippable
  validators + django-lifecycle base model); `starts_with()`/`get_object_or_none()` ORM
  helpers; `track_events()` for django-pghistory; `mjml_template()`/`text_template()` email
  renderers; `AllowedCharactersValidator`; skippable validators
  (`make_skippable`, `SkipFieldValidators`, `SkipNamedValidators`,
  `SkippableValidatorsMixin`).
- `isik.django.drf`: `make_filters()`; `PageNumberPagination`; permissions (`ReadOnly`,
  `IsAnonymous`, `IsSuperUser`, `IsAuthenticatedANDSignupCompleted`, `is_owner()`,
  `prevent_actions()`, `user_property()`).
- `isik.sentry.utils`.
- `django_to_drf_validation_error`, a utility turning Django validation errors into DRF
  validation errors.

### Changed

- `exceptions.py` renamed to `error_handling.py`; `TransformExceptions` now supports
  two-step building - skip passing `transform` and instead decorate a named function later,
  useful when the transform is more than a one-liner.

### Fixed

- Generic foreign key resolution now finds models from strings properly.
- `MediaWhiteNoise` internal methods updated to match the latest Whitenoise version.
- Skippable validators now use the class-prepared signal to set up the skippable feature,
  compatible with Django's metaclasses.
