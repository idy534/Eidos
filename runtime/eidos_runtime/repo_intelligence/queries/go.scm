; Small Eidos projection adapted from tree-sitter-go tags.scm.
(function_declaration name: (identifier) @name.function) @definition.function
(method_declaration name: (field_identifier) @name.method) @definition.method
(type_declaration (type_spec name: (type_identifier) @name.type)) @definition.type
(import_declaration) @import
(call_expression function: (identifier) @reference.call)
(call_expression function: (selector_expression field: (field_identifier) @reference.call))
(type_identifier) @reference.type
(ERROR) @error
