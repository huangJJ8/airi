import type { FieldComparison, JoinSpec, MetricIR } from '../types/airi'

/**
 * Pure presentation helpers for the Metric IR viewer. No business semantics:
 * these only restate what the backend already declared in the IR.
 */

export function describeJoin(join: JoinSpec): string {
  const conditions = join.conditions
    .map((c) => `${c.left.alias}.${c.left.field} = ${c.right.alias}.${c.right.field}`)
    .join(' AND ')
  return `${join.source.database}.${join.source.table} AS ${join.alias} ON ${conditions}`
}

export function describeExclusion(filter: FieldComparison): string {
  return `${filter.left.alias}.${filter.left.field} ${filter.operator} ${filter.right.alias}.${filter.right.field}`
}

/** Extra summary rows a relationship metric adds beyond the single-source ones. */
export function relationshipRows(ir: MetricIR): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = []
  if (!ir.joins?.length) return rows
  rows.push(
    ...ir.joins.map((join) => ({
      label: `Join (${join.join_type.toUpperCase()})`,
      value: describeJoin(join),
    })),
  )
  rows.push(
    ...(ir.column_filters ?? []).map((filter) => ({
      label: 'Self-exclusion',
      value: describeExclusion(filter),
    })),
  )
  return rows
}
