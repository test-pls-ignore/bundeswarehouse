'use server'

const RAG_API_URL = process.env.RAG_API_URL ?? 'http://localhost:8000'

export type Source = {
    chunk_id: string
    doc_id: string
    source_type: 'plenarprotokoll' | 'drucksache'
    speaker: string | null
    titel: string | null
    datum: string
    wahlperiode: number
    pdf_url: string | null
    score: number
    snippet: string
}

export type AskResult = {
    text: string
    sources: Source[]
}

export async function askRag(question: string, wahlperiode?: number): Promise<AskResult> {
    const res = await fetch(`${RAG_API_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, wahlperiode: wahlperiode ?? null }),
    })
    if (!res.ok) {
        const detail = await res.text().catch(() => res.statusText)
        throw new Error(`RAG API error ${res.status}: ${detail}`)
    }
    return res.json()
}
