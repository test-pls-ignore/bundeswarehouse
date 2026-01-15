'use client'

import { useState } from 'react'
import { searchBundestag } from './actions'
import { isValidUrl } from '../lib/validation'
import type { SearchResults } from '../lib/types'

export default function Home() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResults | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    
    try {
      const data = await searchBundestag(query)
      setResults(data)
    } catch (err) {
      // Display user-friendly error message
      setError(err instanceof Error ? err.message : 'An unexpected error occurred')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
      <header className="mb-12 text-center">
        <h1 className="text-4xl font-extrabold text-gray-900 mb-2">Bundestag Warehouse</h1>
        <p className="text-gray-600">Search through documents, processes, and activities</p>
      </header>

      <form onSubmit={handleSearch} className="w-full max-w-2xl mb-12">
        <div className="relative">
          <input
            type="text"
            className="w-full p-4 pl-6 rounded-full shadow-lg border-2 border-transparent focus:border-blue-500 focus:outline-none text-lg text-gray-800"
            placeholder="Search e.g. 'Klimaschutz'..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={200}
            aria-label="Search query"
          />
          <button
            type="submit"
            disabled={loading}
            className="absolute right-2 top-2 bottom-2 bg-blue-600 text-white px-6 rounded-full font-medium hover:bg-blue-700 transition disabled:opacity-50"
            aria-label="Submit search"
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </div>
      </form>

      {error && (
        <div className="w-full max-w-2xl mb-8 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
          {error}
        </div>
      )}

      {results && (
        <div className="w-full max-w-5xl space-y-12">

          {results.vorgaenge.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2">Vorgänge</h2>
              <div className="grid gap-4">
                {results.vorgaenge.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100">
                    <div className="text-sm text-blue-600 font-semibold mb-1">{item.typ}</div>
                    <h3 className="text-lg font-bold text-gray-900 mb-2">{item.titel}</h3>
                    <div className="text-sm text-gray-500 flex justify-between">
                      <span>{item.vorgangstyp}</span>
                      <span>{item.datum ? new Date(item.datum).toLocaleDateString() : ''}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {results.dokumente.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2">Dokumente</h2>
              <div className="grid gap-4">
                {results.dokumente.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100 group">
                    <div className="flex justify-between items-start">
                      <div>
                        <div className="text-sm text-purple-600 font-semibold mb-1">{item.drucksachetyp} {item.nummer}</div>
                        <h3 className="text-lg font-bold text-gray-900 mb-2 group-hover:text-purple-700">{item.titel}</h3>
                      </div>
                      {item.pdf_url && isValidUrl(item.pdf_url, ['bundestag.de']) && (
                        <a href={item.pdf_url} target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-red-500">
                          PDF ↗
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {results.aktivitaeten.length > 0 && (
            <section>
              <h2 className="text-2xl font-bold text-gray-800 mb-4 border-b pb-2">Aktivitäten</h2>
              <div className="grid gap-4">
                {results.aktivitaeten.map((item) => (
                  <div key={item.id} className="bg-white p-6 rounded-xl shadow-sm hover:shadow-md transition border border-gray-100">
                    <div className="text-sm text-green-600 font-semibold mb-1">{item.art}</div>
                    <h3 className="text-lg font-bold text-gray-900 mb-2">{item.titel}</h3>
                    <div className="text-sm text-gray-500">
                      {item.person} • {item.datum ? new Date(item.datum).toLocaleDateString() : ''}
                    </div>
                  </div>
                ))}
              </div>
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
