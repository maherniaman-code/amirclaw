/**
 * Web Scraping / Content Extractor Tool.
 * Fetches and cleans readable text from any URL without needing external headless browsers.
 */
import { assertPublicUrl } from '../security.js';

export interface ScrapeResult {
    url: string;
    title: string;
    description?: string;
    content: string;
    length: number;
}

export async function scrapeWebpage(url: string, maxCharacters: number = 7000): Promise<ScrapeResult | null> {
    try {
        // Follow redirects by hand so every hop is checked: public internet only
        let target = await assertPublicUrl(url);
        let response: Response | null = null;
        for (let hop = 0; hop < 5; hop++) {
            response = await fetch(target, {
                redirect: 'manual',
                headers: {
                    'User-Agent':
                        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'ar,en-US,en;q=0.9',
                },
                signal: AbortSignal.timeout(15000), // 15s timeout
                });
            const next = response.status >= 300 && response.status < 400 ? response.headers.get('location') : null;
            if (!next) break;
            target = await assertPublicUrl(new URL(next, target).toString());
        }
        if (!response) throw new Error('No response');

        if (!response.ok) {
            throw new Error(`HTTP ${response.status} ${response.statusText}`);
        }

        const html = await response.text();

        // Extract title
        const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
        const title = titleMatch ? titleMatch[1].replace(/<[^>]+>/g, '').trim() : '';

        // Extract meta description
        const metaDescMatch =
            html.match(/<meta[^>]*name=["']description["'][^>]*content=["']([^"']*)["']/i) ||
            html.match(/<meta[^>]*content=["']([^"']*)["'][^>]*name=["']description["']/i);
        const description = metaDescMatch ? metaDescMatch[1].trim() : undefined;

        // Clean HTML to extract pure content
        let cleanText = html
            // Remove scripts & styles
            .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
            .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '')
            .replace(/<svg\b[^<]*(?:(?!<\/svg>)<[^<]*)*<\/svg>/gi, '')
            // Remove navigational & boilerplate tags
            .replace(/<nav\b[^<]*(?:(?!<\/nav>)<[^<]*)*<\/nav>/gi, '')
            .replace(/<footer\b[^<]*(?:(?!<\/footer>)<[^<]*)*<\/footer>/gi, '')
            .replace(/<header\b[^<]*(?:(?!<\/header>)<[^<]*)*<\/header>/gi, '')
            .replace(/<aside\b[^<]*(?:(?!<\/aside>)<[^<]*)*<\/aside>/gi, '')
            // Replace block elements with newlines
            .replace(/<\/(p|div|h[1-6]|li|tr|blockquote)>/gi, '\n')
            .replace(/<br\s*[\/]?>/gi, '\n')
            // Remove remaining HTML tags
            .replace(/<[^>]+>/g, ' ')
            // Decode common HTML entities
            .replace(/&nbsp;/g, ' ')
            .replace(/&amp;/g, '&')
            .replace(/&quot;/g, '"')
            .replace(/&#39;/g, "'")
            .replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>')
            // Normalize whitespace
            .replace(/[ \t]+/g, ' ')
            .replace(/\n\s*\n\s*\n+/g, '\n\n')
            .trim();

        // Truncate if too long
        if (cleanText.length > maxCharacters) {
            cleanText = cleanText.slice(0, maxCharacters) + '\n\n... [تم اختصار باقي المحتوى لتجاوز الحد الأقصى]';
        }

        return {
            url,
            title,
            description,
            content: cleanText,
            length: cleanText.length,
        };
    } catch (err: any) {
        console.error(`Error scraping URL (${url}):`, err.message);
        return null;
    }
}

export const scrapeWebpageTool = {
    name: 'scrape_webpage',
    description:
        'Scrape and extract readable article/page text from any web URL. ' +
        'Use this tool whenever the user provides a link to read, summarize, analyze, or study.',
    parameters: {
        type: 'object',
        properties: {
            url: {
                type: 'string',
                description: 'The full HTTP or HTTPS URL of the webpage or article to scrape.',
            },
            maxCharacters: {
                type: 'number',
                description: 'Maximum characters of text to extract (default 7000).',
            },
        },
        required: ['url'],
    },
    execute: async (args: { url: string; maxCharacters?: number }) => {
        let { url, maxCharacters } = args;
        url = url.trim();

        if (!url.startsWith('http://') && !url.startsWith('https://')) {
            url = 'https://' + url;
        }

        console.log(`🌐 Scraping webpage: "${url}"`);
        const result = await scrapeWebpage(url, maxCharacters || 7000);

        if (!result || !result.content) {
            return {
                success: false,
                url,
                message: `❌ تعذر استخراج المحتوى من الرابط "${url}". قد يكون الموقع محميًا أو يتطلب تسجيل دخول.`,
            };
        }

        return {
            success: true,
            url: result.url,
            title: result.title,
            description: result.description,
            content: result.content,
            message: `📄 تم استخراج محتوى الصفحة بنجاح:\n**العنوان:** ${result.title || 'بدون عنوان'}\n\n**المحتوى:**\n${result.content.slice(0, 500)}...`,
        };
    },
};
