"""Build the reviewed static replay payload from repository-root source."""

from scoperelay.evaluation.public_showcase import build


if __name__ == "__main__":
    print(f"Static showcase built at {build()}")
