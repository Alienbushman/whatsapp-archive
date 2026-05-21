export const getChats = () =>
  fetch('/api/chats').then(r => { if (!r.ok) throw r; return r.json() })

export const getMessages = (chatId, { page = 1, pageSize = 50, q = '' } = {}) =>
  fetch(
    `/api/chats/${chatId}/messages?page=${page}&page_size=${pageSize}&q=${encodeURIComponent(q)}`
  ).then(r => { if (!r.ok) throw r; return r.json() })

export const search = (q, chatId = '', kind = 'all') =>
  fetch(`/api/search?q=${encodeURIComponent(q)}&chat_id=${encodeURIComponent(chatId)}&kind=${kind}`)
    .then(r => { if (!r.ok) throw r; return r.json() })

export const fuzzySearch = (q, kind = 'all', limit = 20) =>
  fetch(`/api/search/fuzzy?q=${encodeURIComponent(q)}&kind=${kind}&limit=${limit}`)
    .then(r => { if (!r.ok) throw r; return r.json() })
