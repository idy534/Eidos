; Small Eidos projection adapted from tree-sitter-typescript tags.scm.
(function_declaration name: (identifier) @name.function) @definition.function
(class_declaration name: (type_identifier) @name.class) @definition.class
(interface_declaration name: (type_identifier) @name.type) @definition.type
(type_alias_declaration name: (type_identifier) @name.type) @definition.type
(method_definition name: (property_identifier) @name.method) @definition.method
(variable_declarator name: (identifier) @name.function value: (arrow_function) @definition.function)
(variable_declarator name: (identifier) @name.variable) @definition.variable
(import_statement) @import
(call_expression function: (identifier) @reference.call)
(call_expression function: (member_expression property: (property_identifier) @reference.call))
(type_identifier) @reference.type
(ERROR) @error
