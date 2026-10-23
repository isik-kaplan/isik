"""What the DRF tests share."""

from isik.django.drf.viewsets import BaseModelViewSet


class UnguardedModelViewSet(BaseModelViewSet):
    """
    `BaseModelViewSet` without its two write guards - an `Idempotency-Key` and `If-Match` on every
    write - for the tests of everything else it does, which write without either. The guards' own tests
    use `BaseModelViewSet` itself.
    """

    is_base_class = True
    idempotent_methods = ()
    precondition_methods = ()
