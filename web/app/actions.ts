'use server'

import { prisma } from '../lib/prisma'
import { validateSearchQuery } from '../lib/validation'
import type { SearchResults } from '../lib/types'

/**
 * Search for Bundestag documents, processes, and activities
 * @param query - The search query string
 * @returns Search results containing matching items
 */
export async function searchBundestag(query: string): Promise<SearchResults> {
    try {
        // Validate and sanitize input
        const sanitizedQuery = validateSearchQuery(query)
        
        // Return empty results for empty queries
        if (!sanitizedQuery) {
            return { vorgaenge: [], dokumente: [], aktivitaeten: [] }
        }

        // Execute searches in parallel for better performance
        const [vorgaenge, dokumente, aktivitaeten] = await Promise.all([
            prisma.vorgang.findMany({
                where: {
                    titel: { contains: sanitizedQuery }
                },
                take: 5,
                select: {
                    id: true,
                    wahlperiode: true,
                    titel: true,
                    datum: true,
                    typ: true,
                    vorgangstyp: true,
                    aktueller_stand: true,
                    inhalt: true,
                    metadata_json: true,
                    created_at: true,
                    updated_at: true
                }
            }),
            prisma.dokument.findMany({
                where: {
                    titel: { contains: sanitizedQuery }
                },
                take: 5,
                select: {
                    id: true,
                    vorgang_id: true,
                    drucksachetyp: true,
                    nummer: true,
                    datum: true,
                    titel: true,
                    autoren: true,
                    pdf_url: true,
                    text_content: true,
                    metadata_json: true,
                    created_at: true
                }
            }),
            prisma.aktivitaet.findMany({
                where: {
                    titel: { contains: sanitizedQuery }
                },
                take: 5,
                select: {
                    id: true,
                    vorgang_id: true,
                    datum: true,
                    person: true,
                    art: true,
                    titel: true,
                    metadata_json: true,
                    created_at: true
                }
            })
        ])

        return { vorgaenge, dokumente, aktivitaeten }
    } catch (error) {
        // Log error for monitoring (in production, use proper logging service)
        console.error('Search error:', error)
        
        // Return user-friendly error without exposing internals
        throw new Error('Search failed. Please try again with a different query.')
    }
}
