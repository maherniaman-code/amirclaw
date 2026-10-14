/**
 * Web Search Tool using DuckDuckGo HTML search.
 * Requires no external API keys and returns fresh web search results.
 */

export interface SearchResult {
    title: string;
    url: string;
    snippet: string;
}

export async function performWebSearch(query: string, limit: number = 5): Promise<SearchResult[]> {
    try {
        const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
        const response = await fetch(searchUrl, {
            headers: {
                'User-Agent':
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ar,en-US,en;q=0.9',
            },
        });

        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
        }

        const html = await response.text();
        const results: SearchResult[] = [];

        // Match result bodies
        const resultBlocks = html.split('<div class="result ');

        for (let i = 1; i < resultBlocks.length && results.length < limit; i++) {
            const block = resultBlocks[i];

            // Match title and URL
            const titleMatch = block.match(/<a rel="nofollow" class="result__a" href="([^"]+)">([\s\S]*?)<\/a>/);
            // Match snippet
            const snippetMatch = block.match(/<a class="result__snippet"[^>]*>([\s\S]*?)<\/a>/);

            if (titleMatch) {
                let rawUrl = titleMatch[1];
                // Extract actual redirect URL if wrapped in DuckDuckGo redirect uddg=...
                const matchUddg = rawUrl.match(/uddg=([^&]+)/);
                const url = matchUddg ? decodeURIComponent(matchUddg[1]) : (rawUrl.startsWith('//') ? `https:${rawUrl}` : rawUrl);

                const cleanTitle = titleMatch[2].replace(/<[^>]+>/g, '').trim();
                const cleanSnippet = snippetMatch
                    ? snippetMatch[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
                    : '';

                if (cleanTitle && url.startsWith('http')) {
                    results.push({
                        title: cleanTitle,
                        url,
                        snippet: cleanSnippet,
                    });
                }
            }
        }

        return results;
    } catch (err: any) {
        console.error('Web search error:', err.message);
        return [];
    }
}

export const searchWebTool = {
    name: 'search_web',
    description:
        'Search the live web for up-to-date information, facts, study materials, research papers, tutorials, news, or articles. ' +
        'Returns top search results with titles, links, and text snippets.',
    parameters: {
        type: 'object',
        properties: {
            query: {
                type: 'string',
                description: 'The search query or keywords in Arabic or English.',
            },
            limit: {
                type: 'number',
                description: 'Number of results to return (default 5, max 10).',
            },
        },
        required: ['query'],
    },
    execute: async (args: { query: string; limit?: number }) => {
        let query = args.query.trim();
        const limit = Math.min(Math.max(args.limit || 5, 1), 10);
        const currentYear = new Date().getFullYear();

        // Modernize query if LLM hallucinated an outdated year (2024/2025) while asking for latest/recent news
        if (/\b(2024|2025)\b/.test(query) && /(latest|news|today|recent|أحدث|أخبار|جديد|اليوم)/i.test(query)) {
            query = query.replace(/\b(2024|2025)\b/g, String(currentYear));
        }

        console.log(`🔍 Searching web for: "${query}" (limit: ${limit})`);
        const results = await performWebSearch(query, limit);

        if (results.length === 0) {
            return {
                query,
                results: [],
                message: `لم يتم العثور على نتائج مباشرة لـ: "${query}". يمكنك تجربة كلمات بحث أخرى.`,
            };
        }

        return {
            query,
            count: results.length,
            results,
            message: `تم العثور على ${results.length} نتيجة بحث:\n` +
                results.map((r, i) => `${i + 1}. **${r.title}**\n🔗 ${r.url}\n📝 ${r.snippet}`).join('\n\n'),
        };
    },
};
