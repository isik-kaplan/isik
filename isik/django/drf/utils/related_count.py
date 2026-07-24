from rest_framework.fields import IntegerField


class ModelRelatedCountField(IntegerField):
    """
    Read-only field reporting the count of a related manager, without pulling the related
    objects themselves into the response.

        comment_count = ModelRelatedCountField(related_name="comments")
    """

    def __init__(self, related_name, **kwargs):
        self.related_name = related_name
        kwargs["source"] = "*"
        kwargs["read_only"] = True
        super().__init__(**kwargs)

    def to_representation(self, value):
        return getattr(value, self.related_name).count()
