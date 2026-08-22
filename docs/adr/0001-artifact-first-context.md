# ADR 0001: Use artifact-first expert context

Status: accepted

OpenRTL will share versioned artifacts, decisions, and evidence through
role-specific context packs. It will not use a shared hidden model memory or raw
conversation transcript as engineering state. This makes expert inputs
reviewable, reproducible, and invalidatable when dependencies change.
