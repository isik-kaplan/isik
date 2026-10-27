# feedback

- [votes/](votes/README.md) - up/downvote through-table + `UserVoteMixin`
- [bookmarks/](bookmarks/README.md) - toggleable bookmark through-table + `UserBookmarkMixin`
- [notes/](notes/README.md) - per-user note log (no unique constraint) + `UserNoteMixin`
- [comments/](comments/README.md) - comment log, plain text or Tiptap/ProseMirror JSON, + `UserCommentMixin`

Every attachment, built or bound:

- [your_own_models.md](your_own_models.md) - `model=`: a model you write, checked against what each maker reads
- [annotations.md](annotations.md) - `with_feedback()`, `ranked()` and the serializer fields reading them
- [viewsets.md](viewsets.md) - `VoteActions`, `BookmarkActions`, `CommentActions`, `NoteActions` on a host's viewset
