"""
Let mutmut mutate decorated functions, instead of skipping them outright.

mutmut skips every `def` carrying a decorator except a lone `@staticmethod`/`@classmethod`. Here that
is every `@wraps` wrapper, every `@property`, `@lru_cache`, `@transaction.atomic` and DRF `@action` -
a run over any of them reports zero mutants and zero survivors, which reads exactly like a function
whose tests kill everything. It is silence, not a clean result. (mutmut 3.8 already recurses into a
decorated class, whose decorator stays on the class while its methods get trampolines.)

The reason is codegen rather than intent. A function becomes the original, a copy per mutant and a
trampoline dispatching between them, and each copy is built from the original with its decorators -
twelve mutants of a `@receiver` would connect thirteen handlers, twelve of a `@property` would leave
the trampoline pointing at property objects it can't call. So here the copies are emitted undecorated
and the decorators stay on the trampoline alone, the one thing that's called: each still runs once.

What a copy keeps:

- `@staticmethod`/`@classmethod` when it's the function's only decorator - mutmut binds those copies
  itself. Stacked with anything else, the outer decorator already passes the class, so the copy is a
  plain function.
- `@makes_exemption(...)`, which only marks the function it's given: the copy is what actually runs,
  and has to be recorded as a maker for its exemptions to be recorded at its caller.

A mutation inside a decorator's own arguments is dropped: a copy carries no decorator for it to apply
to, so it would be a twin of the original that no test could ever kill.

A memoizer (`@lru_cache`) sits in front of the dispatch, so a mutant is only tested if its cache starts
empty - tests/conftest.py clears isik's before every test.

mutmut has no plugin hook for this - `[tool.mutmut]` only reaches the pytest side of a run - so
`scripts/mutation_run.py` installs it before mutmut generates anything.
"""

import libcst as cst


BINDING_DECORATORS = frozenset({"staticmethod", "classmethod"})
MARKING_DECORATORS = frozenset({"makes_exemption"})


def _name_of(decorator):
    """The decorator's last name - `@functools.cache` reads as `cache` - or None for anything else."""
    while isinstance(decorator, cst.Call):
        decorator = decorator.func
    if isinstance(decorator, cst.Attribute):
        return decorator.attr.value
    if isinstance(decorator, cst.Name):
        return decorator.value
    return None


def _is_binding(decorator):
    return isinstance(decorator, cst.Name) and decorator.value in BINDING_DECORATORS


def carried_by_copies(decorators):
    """The decorators a mutant copy keeps - see the module docstring."""
    binding = len(decorators) == 1 and _is_binding(decorators[0].decorator)
    return [
        d for d in decorators if (binding and _is_binding(d.decorator)) or _name_of(d.decorator) in MARKING_DECORATORS
    ]


def install():
    """Patch mutmut's code generation in this process. Idempotent."""
    from mutmut.mutation import file_mutation

    if getattr(file_mutation, "_stock_mutmut", None):
        return

    skip_original = file_mutation.MutationVisitor._skip_node_and_children
    arrange_original = file_mutation.function_trampoline_arrangement

    def _skip_node_and_children(self, node):
        if isinstance(node, cst.Decorator):
            return True
        if isinstance(node, cst.FunctionDef) and node.decorators:
            # Every other rule mutmut has, asked as if the decorators weren't there.
            return skip_original(self, node.with_changes(decorators=[]))
        return skip_original(self, node)

    def function_trampoline_arrangement(function, mutants, class_name):
        empty, methods, assignments, names = arrange_original(
            function.with_changes(decorators=carried_by_copies(function.decorators)), mutants, class_name
        )
        # The first node is the trampoline, the one thing that's called - it keeps every decorator, and
        # mutmut's own marking of a lone classmethod.
        trampoline_decorators = [*function.decorators, methods[0].decorators[-1]]
        return empty, [methods[0].with_changes(decorators=trampoline_decorators), *methods[1:]], assignments, names

    file_mutation.MutationVisitor._skip_node_and_children = _skip_node_and_children
    file_mutation.function_trampoline_arrangement = function_trampoline_arrangement
    file_mutation._stock_mutmut = (skip_original, arrange_original)


def uninstall():
    """Put stock mutmut back. Idempotent."""
    from mutmut.mutation import file_mutation

    stock = getattr(file_mutation, "_stock_mutmut", None)
    if not stock:
        return
    file_mutation.MutationVisitor._skip_node_and_children, file_mutation.function_trampoline_arrangement = stock
    del file_mutation._stock_mutmut
