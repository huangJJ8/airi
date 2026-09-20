from pydantic import TypeAdapter

from airi.core.exceptions import DuplicateRegistrationError, RegistryNotFoundError
from airi.core.schemas import VersionedReference
from airi.skills.models import CapabilitySkill, ScenarioSkill, Skill


class SkillRegistry:
    """One namespace across skill types; references are pinned, never 'latest'."""

    def __init__(self) -> None:
        self._skills: dict[tuple[str, str], Skill] = {}

    def register(self, skill: Skill) -> None:
        if not isinstance(skill, (ScenarioSkill, CapabilitySkill)):
            raise TypeError("Expected a scenario or capability skill")
        snapshot = TypeAdapter(Skill).validate_python(skill.model_dump())
        key = (snapshot.name, snapshot.version)
        if key in self._skills:
            raise DuplicateRegistrationError(f"Skill already registered: {key[0]}@{key[1]}")
        self._skills[key] = snapshot

    def register_shared(self, skill: Skill) -> None:
        """Register a capability several scenarios legitimately reference.

        Two scenarios sharing a capability must declare it identically; the same
        name@version is therefore accepted once and only once, and a conflicting
        redeclaration still fails.
        """
        snapshot = TypeAdapter(Skill).validate_python(skill.model_dump())
        key = (snapshot.name, snapshot.version)
        existing = self._skills.get(key)
        if existing is None:
            self._skills[key] = snapshot
            return
        if existing.model_dump() != snapshot.model_dump():
            raise DuplicateRegistrationError(f"Conflicting redeclaration: {key[0]}@{key[1]}")

    def get(self, name: str, version: str) -> Skill:
        reference = VersionedReference(name=name, version=version)
        try:
            return self._skills[(reference.name, reference.version)].model_copy(deep=True)
        except KeyError:
            raise RegistryNotFoundError(f"Skill not found: {name}@{version}") from None

    def list_skills(self) -> list[Skill]:
        return [self._skills[key].model_copy(deep=True) for key in sorted(self._skills)]
