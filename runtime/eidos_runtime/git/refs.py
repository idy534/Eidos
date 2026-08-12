from __future__ import annotations

from dulwich.refs import Ref, check_ref_format, is_local_branch, local_branch_name


class GitRefValidator:
    """Translate user branch names to Dulwich refs.

    Dulwich owns Git's ref grammar.  Eidos only keeps the product boundary
    that this input is a short, user-created local branch name.
    """

    @staticmethod
    def branch(value: str) -> Ref:
        if not isinstance(value, str) or not value or value.startswith("refs/"):
            raise ValueError("Git branch is invalid")
        try:
            ref = local_branch_name(value.encode("utf-8"))
        except (AttributeError, UnicodeEncodeError, TypeError, ValueError) as error:
            raise ValueError("Git branch is invalid") from error
        if not is_local_branch(ref) or not check_ref_format(ref):
            raise ValueError("Git branch is invalid")
        return Ref(ref)

    @staticmethod
    def revision(value: str) -> str:
        """Validate only the application input shape.

        Revision resolution is delegated to Dulwich.  This method does not
        copy Git's revision or ref grammar.
        """

        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("Git revision is invalid")
        return value


__all__ = ["GitRefValidator"]
