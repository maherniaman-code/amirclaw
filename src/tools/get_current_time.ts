export const getCurrentTimeTool = {
    name: 'get_current_time',
    description: 'Get the current local time. Useful when you need to know the time or date.',
    execute: async (_args?: any) => {
        return new Date().toISOString();
    },
    parameters: {
        type: 'object',
        properties: {},
        required: []
    }
};
