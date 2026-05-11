'use client'

import { useState } from 'react'
import Link from 'next/link'
import { searchBundestag, loadMore, type ContentMatch } from './actions'

type Category = 'vorgaenge' | 'dokumente' | 'aktivitaeten'

type VorgangItem = { id: string; vorgangstyp: string; titel: string; datum: string | null }
type DokumentItem = { id: string; drucksachetyp: string; dokumentnummer: string; titel: string; pdf_url: string | null }
type AktivitaetItem = { id: string; aktivitaetsart: string; person_name: string; datum: string | null }

type Results = {
  vorgaenge: VorgangItem[]
  dokumente: DokumentItem[]
  aktivitaeten: AktivitaetItem[]
  contentMatches: ContentMatch[]
  ragUnavailable: boolean
  counts: { vorgaenge: number; dokumente: number; aktivitaeten: number; contentMatches: number }
}

export default function Home() {
  const [queryText, setQueryText] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState<Partial<Record<Category, boolean>>>({})
  const [wahlperiode, setWahlperiode] = useState<number>(20)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const data = await searchBundestag(queryText, wahlperiode)
      setResults(data)
      setActiveQuery(queryText)
    } finally {
      setLoading(false)
    }
  }

  const handleLoadMore = async (category: Category) => {
    if (!results) return
    setLoadingMore(prev => ({ ...prev, [category]: true }))
    try {
      const more = await loadMore(category, activeQuery, results[category].length, wahlperiode)
      setResults(prev => prev ? { ...prev, [category]: [...prev[category], ...more] } : prev)
    } finally {
      setLoadingMore(prev => ({ ...prev, [category]: false }))
    }
  }

  const totalHits = results
    ? results.counts.vorgaenge + results.counts.dokumente + results.counts.aktivitaeten
    : 0

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-extrabold text-gray-900 mb-2">Bundestag Warehouse</h1>
        <p className="text-gray-600">Search through documents, processes, and activities</p>
        <div className="mt-3 flex gap-4 justify-center">
          <Link href="/ask" className="text-sm text-indigo-600 hover:underline">
            Ask a question →
          </Link>
          <Link href="/analytics" className="text-sm text-emerald-600 hover:underline">
            Analytics →
          </Link>
          <Link href="/plenarprotokoll" className="text-sm text-orange-600 hover:underline">
            Plenarprotokoll-Browser →
          </Link>
        </div>
      </header>

      <form onSubmit={handleSearch} className="w-full max-w-2xl mb-6">
        <div className="mb-2 flex items-center justify-end gap-2">
          <label className="text-sm text-gray-500" htmlFor="wahlperiode-select">Wahlperiode</label>
          <select
            id="wahlperiode-select"
            value={wahlperiode}
            onChange={(e) => setWahlperiode(Number(e.target.value))}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700"
          >
            <option value={20}>WP 20</option>
            <option value={19}>WP 19</option>
          </select>
        </div>
        <div className="relative">
          <input
            type="text"
            className="w-full p-4 pl-6 rounded-full shadow-lg border-2 border-transparent focus:border-blue-500 focus:outline-none text-lg text-gray-800"
            placeholder="Search e.g. 'Klimaschutz'..."
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
          />
          <button
            type="submit"
            disabled={loading}
            className="absolute right-2 top-2 bottom-2 bg-blue-600 text-white px-6 rounded-full font-medium hover:bg-blue-700 transition disabled:opacity-50"
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </div>
      </form>

      {results && totalHits > 0 && (
        <div className="w-full max-w-5xl mb-8 bg-white rounded-xl shadow-sm border border-gray-100 p-4 flex flex-wrap gap-6 items-center text-sm">
          <span className="text-blue-600 font-semibold">
            {results.counts.vorgaenge.toLocaleString('de-DE')} Vorgänge
          </span>
          <span className="text-purple-600 font-semibold">
            {results.counts.dokumente.toLocaleString('de-DE')} Dokumente
          </span>
          <span className="text-green-600 font-semibold">
            {results.counts.aktivitaeten.toLocaleString('de-DE')} Aktivitäten
          </span>
          <span className="text-indigo-600 font-semibold">
            {results.counts.contentMatches.toLocaleString('de-DE')} Dokumentinhalte
          </span>
          <span className="ml-auto text-gray-400">
            {totalHits.toLocaleString('de-DE')} total matches
          </span>
        </div>
      )}

      {results && (
        <div className="w-full max-w-5xl space-y-12">
          {results.contentMatches.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2 flex justify-between items-baseline">
                <span>Dokumentinhalte (RAG)</span>
                <span className="text-sm font-normal text-gray-400">
                  {results.contentMatches.length.toLocaleString('de-DE')} Treffer
                </span>
              </h2>
              <div className="grid gap-4">
                {results.contentMatches.map((item) => (
                  <div key={item.chunk_id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100">
                    <div className="flex items-center gap-2 mb-2 text-xs text-indigo-600">
                      <span className="font-semibold">{item.source_type === 'plenarprotokoll' ? 'Plenarprotokoll' : 'Drucksache'}</span>
                      <span className="text-gray-400">WP {item.wahlperiode}</span>
                      <span className="text-gray-400">score {item.score.toFixed(2)}</span>
                    </div>
                    <h3 className="text-lg font-bold text-gray-900 mb-2">{item.titel ?? item.doc_id}</h3>
                    <p className="text-sm text-gray-600 line-clamp-4">{item.snippet}</p>
                    {item.pdf_url && (
                      <a href={item.pdf_url} target="_blank" rel="noreferrer" className="inline-block mt-3 text-xs text-gray-500 hover:text-red-500">
                        PDF ↗
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
          {results.ragUnavailable && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
              Dokumentinhalte sind derzeit nicht verfügbar.
            </div>
          )}

          {results.vorgaenge.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2 flex justify-between items-baseline">
                <span>Vorgänge</span>
                <span className="text-sm font-normal text-gray-400">
                  {results.vorgaenge.length} of {results.counts.vorgaenge.toLocaleString('de-DE')}
                </span>
              </h2>
              <div className="grid gap-4">
                {results.vorgaenge.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100">
                    <div className="text-sm text-blue-600 font-semibold mb-1">{item.vorgangstyp}</div>
                    <h3 className="text-lg font-bold text-gray-900 mb-2">{item.titel}</h3>
                    <div className="text-sm text-gray-500">
                      {item.datum ? new Date(item.datum).toLocaleDateString('de-DE') : ''}
                    </div>
                  </div>
                ))}
              </div>
              {results.vorgaenge.length < results.counts.vorgaenge && (
                <button
                  onClick={() => handleLoadMore('vorgaenge')}
                  disabled={loadingMore.vorgaenge}
                  className="mt-4 w-full py-2 rounded-lg border-2 border-blue-200 text-blue-600 font-medium hover:bg-blue-50 transition disabled:opacity-50"
                >
                  {loadingMore.vorgaenge
                    ? 'Loading...'
                    : `Load more (${(results.counts.vorgaenge - results.vorgaenge.length).toLocaleString('de-DE')} remaining)`}
                </button>
              )}
            </section>
          )}

          {results.dokumente.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2 flex justify-between items-baseline">
                <span>Dokumente</span>
                <span className="text-sm font-normal text-gray-400">
                  {results.dokumente.length} of {results.counts.dokumente.toLocaleString('de-DE')}
                </span>
              </h2>
              <div className="grid gap-4">
                {results.dokumente.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100 group">
                    <div className="flex justify-between items-start">
                      <div>
                        <div className="text-sm text-purple-600 font-semibold mb-1">
                          {item.drucksachetyp} {item.dokumentnummer}
                        </div>
                        <h3 className="text-lg font-bold text-gray-900 mb-2 group-hover:text-purple-700">{item.titel}</h3>
                      </div>
                      {item.pdf_url && (
                        <a href={item.pdf_url} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-red-500 shrink-0 ml-4">
                          PDF ↗
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {results.dokumente.length < results.counts.dokumente && (
                <button
                  onClick={() => handleLoadMore('dokumente')}
                  disabled={loadingMore.dokumente}
                  className="mt-4 w-full py-2 rounded-lg border-2 border-purple-200 text-purple-600 font-medium hover:bg-purple-50 transition disabled:opacity-50"
                >
                  {loadingMore.dokumente
                    ? 'Loading...'
                    : `Load more (${(results.counts.dokumente - results.dokumente.length).toLocaleString('de-DE')} remaining)`}
                </button>
              )}
            </section>
          )}

          {results.aktivitaeten.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2 flex justify-between items-baseline">
                <span>Aktivitäten</span>
                <span className="text-sm font-normal text-gray-400">
                  {results.aktivitaeten.length} of {results.counts.aktivitaeten.toLocaleString('de-DE')}
                </span>
              </h2>
              <div className="grid gap-4">
                {results.aktivitaeten.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100">
                    <div className="text-sm text-green-600 font-semibold mb-1">{item.aktivitaetsart}</div>
                    <h3 className="text-lg font-bold text-gray-900 mb-2">{item.person_name}</h3>
                    <div className="text-sm text-gray-500">
                      {item.datum ? new Date(item.datum).toLocaleDateString('de-DE') : ''}
                    </div>
                  </div>
                ))}
              </div>
              {results.aktivitaeten.length < results.counts.aktivitaeten && (
                <button
                  onClick={() => handleLoadMore('aktivitaeten')}
                  disabled={loadingMore.aktivitaeten}
                  className="mt-4 w-full py-2 rounded-lg border-2 border-green-200 text-green-600 font-medium hover:bg-green-50 transition disabled:opacity-50"
                >
                  {loadingMore.aktivitaeten
                    ? 'Loading...'
                    : `Load more (${(results.counts.aktivitaeten - results.aktivitaeten.length).toLocaleString('de-DE')} remaining)`}
                </button>
              )}
            </section>
          )}

          {results.vorgaenge.length === 0 && results.dokumente.length === 0 && results.aktivitaeten.length === 0 && results.contentMatches.length === 0 && (
            <div className="text-center text-gray-500 text-lg mt-8">
              No results found. Try a different term.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
