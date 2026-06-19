/**
 * SHACL_EXAMPLE_TEMPLATE — copy-paste starter shape for the raw .ttl mode.
 *
 * This is a TEXT TEMPLATE only.  It is NOT a built-in enforced rule pack.
 * Copy it into the raw TTL textarea, edit the shape name / path / constraint
 * to match your policy, then click "활성화" to save it as a custom rule.
 *
 * The shape is modelled on the magi:Evidence ontology used by the runtime:
 *   - Evidence nodes carry rdf:type magi:Evidence.
 *   - Numeric fields are exposed as magi:field_<key> predicates
 *     (e.g. magi:field_amount, magi:field_count, magi:field_total).
 * This example enforces that field_amount must not exceed 3000.
 */

export const SHACL_EXAMPLE_TEMPLATE: string = `\
# SHACL example — copy and edit to fit your policy.
# This template is NOT automatically enforced; save it via "활성화" to activate.
#
# Ontology notes:
#   magi:Evidence  — every evidence record is an instance of this class.
#   magi:field_*   — numeric / string fields from the record (e.g. field_amount).
#   sh:maxInclusive — blocks when the field value exceeds the limit.

@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix magi: <https://openmagi.ai/ns/evidence#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

# Rename magi:AmountShape to something descriptive for your policy.
magi:AmountShape
    a sh:NodeShape ;
    sh:targetClass magi:Evidence ;
    sh:property [
        sh:path        magi:field_amount ;
        sh:maxInclusive 3000 ;
        sh:message      "amount must not exceed 3000" ;
    ] .
`;
