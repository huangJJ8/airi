import { describe, expect, it } from 'vitest'
import type { MetricIR } from '../types/airi'
import { describeExclusion, describeJoin, relationshipRows } from './metricIr'

const RELATION_IR = {
  name: 'related_enterprise_count',
  display_name: '企业关联企业数量',
  description: 'synthetic',
  entity_key: 'enterprise_id',
  source: { catalog: null, database: 'demo', table: 'enterprise_person_relation' },
  aggregation: { function: 'count_distinct', field: 'related_enterprise_id' },
  source_alias: 'ep',
  aggregation_alias: 'pe',
  joins: [
    {
      alias: 'pe',
      join_type: 'inner' as const,
      source: { catalog: null, database: 'demo', table: 'person_enterprise_relation' },
      conditions: [
        {
          left: { alias: 'ep', field: 'person_id' },
          operator: '=' as const,
          right: { alias: 'pe', field: 'person_id' },
        },
      ],
    },
  ],
  column_filters: [
    {
      left: { alias: 'pe', field: 'related_enterprise_id' },
      operator: '<>' as const,
      right: { alias: 'ep', field: 'enterprise_id' },
    },
  ],
}

const SINGLE_SOURCE_IR = {
  name: 'invoice_amount_30d',
  description: 'synthetic',
  entity_key: 'seller_tax_no',
  source: { catalog: null, database: 'c_db', table: 'source_fp_jdc_view' },
  aggregation: { function: 'sum', field: 'invoice_amt' },
}

describe('describeJoin', () => {
  it('restates the declared join edge, source first', () => {
    expect(describeJoin(RELATION_IR.joins[0])).toBe(
      'demo.person_enterprise_relation AS pe ON ep.person_id = pe.person_id',
    )
  })
})

describe('describeExclusion', () => {
  it('restates the declared self-exclusion comparison', () => {
    expect(describeExclusion(RELATION_IR.column_filters[0])).toBe(
      'pe.related_enterprise_id <> ep.enterprise_id',
    )
  })
})

describe('relationshipRows', () => {
  it('adds join and self-exclusion rows for a relationship metric', () => {
    const rows = relationshipRows(RELATION_IR as unknown as MetricIR)
    expect(rows).toHaveLength(2)
    expect(rows[0]).toEqual({
      label: 'Join (INNER)',
      value: 'demo.person_enterprise_relation AS pe ON ep.person_id = pe.person_id',
    })
    expect(rows[1]).toEqual({ label: 'Self-exclusion', value: 'pe.related_enterprise_id <> ep.enterprise_id' })
  })

  it('adds nothing for a single-source metric', () => {
    expect(relationshipRows(SINGLE_SOURCE_IR as unknown as MetricIR)).toEqual([])
  })
})
