'use client'

import { useState } from 'react'
import Link from 'next/link'
import { searchBundestag, loadMore } from './actions'

type Category = 'vorgaenge' | 'dokumente' | 'aktivitaeten'

type Results = {
  vorgaenge: any[]
  dokumente: any[]
  aktivitaeten: any[]
  counts: { vorgaenge: number; dokumente: number; aktivitaeten: number }
}

export default function Home() {
  const [queryText, setQueryText] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState<Partial<Record<Category, boolean>>>({})

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const data = await searchBundestag(queryText)
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
      const more = await loadMore(category, activeQuery, results[category].length)
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
        <div className="mt-3">
          <Link href="/plenarprotokoll" className="text-sm text-orange-600 hover:underline">
            Plenarprotokoll-Browser →
          </Link>
        </div>
      </header>

      <form onSubmit={handleSearch} className="w-full max-w-2xl mb-6">
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
          <span className="ml-auto text-gray-400">
            {totalHits.toLocaleString('de-DE')} total matches
          </span>
        </div>
      )}

      {results && (
        <div className="w-full max-w-5xl space-y-12">

          {results.vorgaenge.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2 flex justify-between items-baseline">
                <span>Vorgänge</span>
                <span className="text-sm font-normal text-gray-400">
                  {results.vorgaenge.length} of {results.counts.vorgaenge.toLocaleString('de-DE')}
                </span>
              </h2>
              <div className="grid gap-4">
                {results.vorgaenge.map((item: any) => (
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
                {results.dokumente.map((item: any) => (
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
                {results.aktivitaeten.map((item: any) => (
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

          {results.vorgaenge.length === 0 && results.dokumente.length === 0 && results.aktivitaeten.length === 0 && (
            <div className="text-center text-gray-500 text-lg mt-8">
              No results found. Try a different term.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
