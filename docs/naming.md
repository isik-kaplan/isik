# Naming

The rules isik's own names follow, written down so they're held rather than followed by habit.
`tests/test_naming.py` fails on the parts a test can see. A rename is a breaking change shipped in a
minor release with no alias, as with every breaking change while isik is at 0.y.z.

1. **Spell words out.** No truncations in a public name: `ContextField`, not `CtxField`;
   `generic_foreign_key`, not `gfk`. A universal, platform-sanctioned abbreviation stays - `id`,
   `env`, `config`, `db` (`django.db`). Python's own `str` doesn't count as one for a class name: the
   standard library spells it out there too (`collections.UserString`).
2. **An acronym keeps its capitals.** In a PascalCase class name it's uppercase -
   `JSONBodyCodec`, `UUIDPrimaryKeyModel`, `HTTPExceptions`. In a snake_case name it's lowercase,
   as everything is - `http_exceptions`, `orm.py`. This is isik-ts's rule in Python's two cases, so
   the two libraries read alike.
3. **American spelling**, in identifiers and in prose: `behavior`, `honor`, `organization`.
4. **A name says what the thing is for** - for a domain type: `NoHelpText`, `IdempotencyKeyReused`.
   Machinery whose whole job *is* its mechanism is named for the mechanism, and that's not a
   violation: `TransformExceptions`, `RequiredAttributesMixin`, `NarrowingFilterMixin`.
5. **A conjunction in capitals is allowed, and it isn't an acronym.** `CookieORHeaderSessionMiddleware`,
   `IsAuthenticatedANDSignupCompleted`: the capitals separate two
   conditions that PascalCase would run together, so they stay.
