; Small Eidos projection adapted from tree-sitter-javascript tags.scm.
(function_declaration name: (identifier) @name.function) @definition.function
(class_declaration name: (identifier) @name.class) @definition.class
(method_definition name: (property_identifier) @name.method) @definition.method
(variable_declarator name: (identifier) @name.function value: (arrow_function) @definition.function)
(variable_declarator name: (identifier) @name.variable) @definition.variable
(import_statement) @import
(call_expression function: (identifier) @reference.call)
(call_expression function: (member_expression property: (property_identifier) @reference.call))
(ERROR) @error
