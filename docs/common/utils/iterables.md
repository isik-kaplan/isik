# iterables

Small helpers for filtering and combining iterables/mappings that come up often enough to not rewrite each time.

## not_none

Predicate: `value is not None`. Mostly passed as `pred` to `first_of` or `filter`.

```python
from isik.common.utils.iterables import not_none

first_valid = next(filter(not_none, [None, None, "x"]))  # "x"
```

## first_of

First truthy value in `iterable` (or the first value matching `pred`, if given), else `default`.

```python
from isik.common.utils.iterables import first_of, not_none

first_of([None, None, "x"])                       # "x"
first_of([None, None], default="fallback", pred=not_none)  # "fallback"
```

## purge_iterable / purge_mapping

Drop specific items from a list, or specific keys from a dict.

```python
from isik.common.utils.iterables import purge_iterable, purge_mapping

purge_iterable([1, 2, 3, 4], [2, 4])       # [1, 3]
purge_mapping({"a": 1, "b": 2}, ["b"])     # {"a": 1}
```

## all_combinations

All non-empty combinations of `options`, of every length from 1 to `len(options)`.

```python
from isik.common.utils.iterables import all_combinations

all_combinations(["a", "b"])  # [["a"], ["b"], ["a", "b"]]
```
