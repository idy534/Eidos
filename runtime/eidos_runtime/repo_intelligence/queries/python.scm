; Small Eidos projection adapted from tree-sitter-python tags.scm.
(class_definition body: (block (function_definition name: (identifier) @name.method) @definition.method))
(function_definition name: (identifier) @name.function) @definition.function
(class_definition name: (identifier) @name.class) @definition.class
(import_statement) @import
(import_from_statement) @import
(call function: (identifier) @reference.call)
(call function: (attribute attribute: (identifier) @reference.call))
(ERROR) @error
