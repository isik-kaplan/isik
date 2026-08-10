import pytest

from isik.common.utils.required_attributes import REQUIRED, RequiredAttributesMixin


class TestRequiredAttributesMixin:
    def test_raises_when_a_required_attribute_is_left_unset(self):
        with pytest.raises(TypeError, match="^Forgetful must define a `name` attribute$"):

            class Base(RequiredAttributesMixin):
                required_attributes = ["name"]
                name = REQUIRED

            class Forgetful(Base):
                pass

    def test_does_not_raise_when_the_required_attribute_is_overridden(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["name"]
            name = REQUIRED

        class Named(Base):
            name = "widget"

        assert Named.name == "widget"

    def test_inheriting_the_override_further_down_is_fine(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["name"]
            name = REQUIRED

        class Named(Base):
            name = "widget"

        class GrandChild(Named):
            pass

        assert GrandChild.name == "widget"

    def test_a_falsy_but_non_sentinel_value_satisfies_the_requirement(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["count"]
            count = REQUIRED

        class Zeroed(Base):
            count = 0

        assert Zeroed.count == 0

    def test_no_required_attributes_means_nothing_to_enforce(self):
        class Base(RequiredAttributesMixin):
            pass

        class Anything(Base):
            pass

        assert Anything is not None

    def test_redeclaring_required_attributes_suppresses_the_check_only_for_that_class(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["name"]
            name = REQUIRED

        class Middle(Base):
            # Redeclares required_attributes to a new list - this exempts Middle from any check
            # at all (even against "name", which is still REQUIRED and unset here), because the
            # exemption is "did this class itself declare required_attributes", not "is this the
            # original declarer".
            required_attributes = ["other"]

        assert Middle.name is REQUIRED

        # A further subclass that does NOT redeclare required_attributes is checked again, against
        # whatever list it inherits - here Middle's ["other"], which GrandChild still lacks.
        with pytest.raises(TypeError, match="must define a `other` attribute"):

            class GrandChild(Middle):
                pass

    def test_is_base_class_exempts_that_class_without_redeclaring_required_attributes(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["name"]
            name = REQUIRED

        class StillAbstract(Base):
            is_base_class = True  # exempt, even though `name` is still unset

        assert StillAbstract.name is REQUIRED

        # A further subclass that doesn't set is_base_class itself is checked normally again.
        with pytest.raises(TypeError, match="must define a `name` attribute"):

            class Forgetful(StillAbstract):
                pass

    def test_is_base_class_exemption_does_not_apply_to_descendants(self):
        class Base(RequiredAttributesMixin):
            required_attributes = ["name"]
            name = REQUIRED

        class StillAbstract(Base):
            is_base_class = True

        class Named(StillAbstract):
            name = "widget"

        assert Named.name == "widget"
