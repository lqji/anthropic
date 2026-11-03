from fastapi import Request

from clinic.deps import Deps


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps
