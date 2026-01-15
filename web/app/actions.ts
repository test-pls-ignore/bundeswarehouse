'use server'

import { PrismaClient } from '@prisma/client'

// Singleton pattern to avoid multiple instances
const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

const prisma = globalForPrisma.prisma ?? new PrismaClient()

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma

// Constants
const MAX_QUERY_LENGTH = 200
const RESULT_LIMIT = 5

export type SearchResults = {
    vorgaenge: Array<{
        id: string
        titel: string | null
        typ: string | null
        vorgangstyp: string | null
        datum: Date | null
    }>
    dokumente: Array<{
        id: string
        titel: string | null
        drucksachetyp: string | null
        nummer: string | null
        pdf_url: string | null
    }>
    aktivitaeten: Array<{
        id: string
        titel: string | null
        art: string | null
        person: string | null
        datum: Date | null
    }>
}

export async function searchBundestag(query: string): Promise<SearchResults> {
    // Input validation
    if (!query || typeof query !== 'string') {
        return { vorgaenge: [], dokumente: [], aktivitaeten: [] }
    }

    // Sanitize and limit query length
    const sanitizedQuery = query.trim().slice(0, MAX_QUERY_LENGTH)
    
    if (sanitizedQuery.length === 0) {
        return { vorgaenge: [], dokumente: [], aktivitaeten: [] }
    }

    const vorgaenge = await prisma.vorgang.findMany({
        where: {
            titel: { contains: sanitizedQuery }
        },
        select: {
            id: true,
            titel: true,
            typ: true,
            vorgangstyp: true,
            datum: true
        },
        take: RESULT_LIMIT
    })

    const dokumente = await prisma.dokument.findMany({
        where: {
            titel: { contains: sanitizedQuery }
        },
        select: {
            id: true,
            titel: true,
            drucksachetyp: true,
            nummer: true,
            pdf_url: true
        },
        take: RESULT_LIMIT
    })

    const aktivitaeten = await prisma.aktivitaet.findMany({
        where: {
            titel: { contains: sanitizedQuery }
        },
        select: {
            id: true,
            titel: true,
            art: true,
            person: true,
            datum: true
        },
        take: RESULT_LIMIT
    })

    return { vorgaenge, dokumente, aktivitaeten }
}
