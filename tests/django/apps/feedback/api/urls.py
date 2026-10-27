from rest_framework.routers import SimpleRouter

from tests.django.apps.feedback.api import views


router = SimpleRouter()
router.register("posts", views.PostViewSet, basename="post")
router.register("threads", views.ThreadViewSet, basename="thread")
router.register("moderated-posts", views.ModeratedPostViewSet, basename="moderated-post")
router.register("articles", views.ArticleViewSet, basename="article")
router.register("answers", views.HelpfulnessViewSet, basename="answer")
# What the schema tests describe.
described = router.urls

# Misconfigured on purpose - served for its refusal, kept out of the schema it can't describe.
unpicked = SimpleRouter()
unpicked.register("unpicked", views.UnpickedViewSet, basename="unpicked")

urlpatterns = [*described, *unpicked.urls]
