"""Vault indexer (ADR-039 action item #2): projects a markdown vault
(Obsidian-compatible frontmatter + wikilinks) into the V0009 notes/links/
tags/chunks tables. Fully rebuildable -- the vault on disk is the source of
truth, the Postgres tables are a derived, disposable projection."""
