'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { browsePlenarprotokolle } from './actions'

type Row = {
  id: string
  dokumentnummer: string
  wahlperiode: number
  datum: string
  sitzungsbemerkung: string | null
  pdf_url: string | null
}

type Filters = {
  wahlperiode: number | undefined
  year: number | undefined
  specialOnly: boolean
}

const WAHLPERIODEN = Array.from({ length: 21 }, (_, i) => 21 - i)

export default function PlenarprotokollBrowser() {
  const [filters, setFilters] = useState<Filters>({ wahlperiode: undefined, year: undefined, specialOnly: false })
  const [pending, setPending] = useState<Filters>({ wahlperiode: undefined, year: undefined, specialOnly: false })
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)

  const fetchInitial = async (f: Filters) => {
    setLoading(true)
    try {
      const data = await browsePlenarprotokolle({ ...f, offset: 0 })
      setRows(data.rows as Row[])
      setTotal(data.total)
      setFilters(f)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchInitial(pending) }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    fetchInitial(pending)
  }

  const handleLoadMore = async () => {
    setLoadingMore(true)
    try {
      const data = await browsePlenarprotokolle({ ...filters, offset: rows.length })
      setRows(prev => [...prev, ...data.rows as Row[]])
    } finally {
      setLoadingMore(false)
    }
  }

  const yearValue = pending.year ?? ''

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
      <header className="w-full max-w-5xl mb-8">
        <div className="flex items-baseline justify-between mb-1">
          <h1 className="text-3xl font-extrabold text-gray-900">Plenarprotokolle</h1>
          <Link href="/" className="text-sm text-blue-600 hover:underline">← Suche</Link>
        </div>
        <p className="text-gray-500 text-sm">Browse all plenary session records from 1949 to present</p>
      </header>

      <form onSubmit={handleSubmit} className="w-full max-w-5xl mb-6 bg-white rounded-xl shadow-sm border border-gray-100 p-4 flex flex-wrap gap-4 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Wahlperiode</label>
          <select
            className="border border-gray-200 rounded-lg px-3 py-2 text-gray-800 text-sm focus:outline-none focus:border-blue-400"
            value={pending.wahlperiode ?? ''}
            onChange={e => setPending(p => ({ ...p, wahlperiode: e.target.value ? Number(e.target.value) : undefined }))}
          >
            <option value="">All</option>
            {WAHLPERIODEN.map(wp => (
              <option key={wp} value={wp}>{wp}. Wahlperiode</option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Jahr</label>
          <input
            type="number"
            min={1949}
            max={2026}
            placeholder="e.g. 2023"
            className="border border-gray-200 rounded-lg px-3 py-2 text-gray-800 text-sm w-32 focus:outline-none focus:border-blue-400"
            value={yearValue}
            onChange={e => setPending(p => ({ ...p, year: e.target.value ? Number(e.target.value) : undefined }))}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Besondere Sitzungen</label>
          <label className="flex items-center gap-2 h-9 cursor-pointer">
            <input
              type="checkbox"
              className="w-4 h-4 accent-orange-500"
              checked={pending.specialOnly}
              onChange={e => setPending(p => ({ ...p, specialOnly: e.target.checked }))}
            />
            <span className="text-sm text-gray-700">nur besondere Sitzungen</span>
          </label>
        </div>

        <button
          type="submit"
          className="ml-auto bg-orange-500 text-white px-5 py-2 rounded-lg font-medium hover:bg-orange-600 transition text-sm"
        >
          Anwenden
        </button>
      </form>

      {loading ? (
        <div className="text-gray-400 mt-12">Loading…</div>
      ) : (
        <div className="w-full max-w-5xl">
          <div className="text-sm text-gray-400 mb-3">
            {total.toLocaleString('de-DE')} Protokolle &mdash; showing {rows.length.toLocaleString('de-DE')}
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-3">Dokument-Nr.</th>
                  <th className="px-4 py-3">Datum</th>
                  <th className="px-4 py-3">Wahlperiode</th>
                  <th className="px-4 py-3">Bemerkung</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {rows.map(row => (
                  <tr key={row.id} className="hover:bg-gray-50 transition">
                    <td className="px-4 py-3 font-mono text-orange-700">{row.dokumentnummer}</td>
                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      {row.datum ? new Date(row.datum).toLocaleDateString('de-DE') : '—'}
                    </td>
                    <td className="px-4 py-3 text-gray-500">{row.wahlperiode ?? '—'}</td>
                    <td className="px-4 py-3 text-gray-600 max-w-sm truncate" title={row.sitzungsbemerkung ?? ''}>
                      {row.sitzungsbemerkung ?? ''}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {row.pdf_url && (
                        <a href={row.pdf_url} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-red-500">
                          PDF ↗
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-12 text-center text-gray-400">
                      No results for these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {rows.length < total && (
            <button
              onClick={handleLoadMore}
              disabled={loadingMore}
              className="mt-4 w-full py-2 rounded-lg border-2 border-orange-200 text-orange-600 font-medium hover:bg-orange-50 transition disabled:opacity-50"
            >
              {loadingMore
                ? 'Loading…'
                : `Load more (${(total - rows.length).toLocaleString('de-DE')} remaining)`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
