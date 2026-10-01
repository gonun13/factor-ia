"""The domain: the entities, the contracts and the rules that read them.

Nothing here performs I/O or imports the web framework, a store or a
subprocess — which is what makes every rule below testable on values alone.
`pydantic` is the one library it leans on, because the data model is written
in it.
"""
