"""
A second test project, with every notifications model - the ledger's eight and the audience's four - owned
by the project under tables of its own, as a project following "own every model" does. Its suite runs in a
process of its own (`tests/test_swapped_project.py` starts it): a model is swapped once, when Django sets
up, so the main suite's defaults and this can't share one.
"""
