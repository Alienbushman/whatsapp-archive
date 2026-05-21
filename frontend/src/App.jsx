import React, { useEffect, useState } from 'react'
import { GroupProvider } from './components/GroupContext.jsx'
import { CollectionsProvider } from './components/CollectionsContext.jsx'
import GroupPanel from './components/GroupPanel.jsx'
import ChatSidebar from './components/ChatSidebar.jsx'
import MessageThread from './components/MessageThread.jsx'
import SearchResults from './components/SearchResults.jsx'
import AskPanel from './components/AskPanel.jsx'
import ExportModal from './components/ExportModal.jsx'
import AuthorPage from './components/AuthorPage.jsx'
import TagBrowsePage from './components/TagBrowsePage.jsx'
import AggregationsPage from './components/AggregationsPage.jsx'
import DashboardPage from './components/DashboardPage.jsx'
import CollectionsPage from './components/CollectionsPage.jsx'
import DigestsPage from './components/DigestsPage.jsx'
import EntitiesPage from './components/EntitiesPage.jsx'
import ScrapeRecoveryPage from './components/ScrapeRecoveryPage.jsx'
import ResearchPage from './components/ResearchPage.jsx'
import EntityHomePage from './components/EntityHomePage.jsx'
import CompareEntitiesPage from './components/CompareEntitiesPage.jsx'
import TopicsPage from './components/TopicsPage.jsx'
import ExportsPage from './components/ExportsPage.jsx'
import ResearchBinsPage from './components/ResearchBinsPage.jsx'
import PipelineProgressBar from './components/PipelineProgressBar.jsx'
import { TweetCard } from './components/LinkPreview.jsx'

function MessageBookmarkCard({ bm, onSelectChat }) {
  return (
    <div className="msg-bookmark-card">
      <div className="msg-bookmark-meta">
        <span className="msg-bookmark-sender">{bm.sender}</span>
        <span className="msg-bookmark-ts">{bm.ts?.slice(0, 16)}</span>
        {onSelectChat && (
          <button
            className="msg-bookmark-open btn-link"
            onClick={() => onSelectChat(bm.chat_id, bm.chat_id)}
          >Open in chat ↗</button>
        )}
      </div>
      <p className="msg-bookmark-body">{bm.body}</p>
      {bm.note && <p className="bookmarks-note">{bm.note}</p>}
    </div>
  )
}

function BookmarksPage({ onSearch, onAuthorClick, onHashtagClick, onMentionClick, onSelectChat }) {
  const [bookmarks, setBookmarks] = useState(null)
  const [msgBookmarks, setMsgBookmarks] = useState(null)

  useEffect(() => {
    fetch('/api/bookmarks?limit=200')
      .then(r => r.ok ? r.json() : [])
      .then(setBookmarks)
      .catch(() => setBookmarks([]))
    fetch('/api/message-bookmarks?limit=200')
      .then(r => r.ok ? r.json() : [])
      .then(setMsgBookmarks)
      .catch(() => setMsgBookmarks([]))
  }, [])

  const totalCount = (bookmarks?.length ?? 0) + (msgBookmarks?.length ?? 0)

  return (
    <div className="bookmarks-page">
      <h2 className="bookmarks-title">★ Bookmarks</h2>
      {(bookmarks === null || msgBookmarks === null) && <div className="loading">Loading…</div>}
      {bookmarks !== null && msgBookmarks !== null && totalCount === 0 && (
        <div className="empty-state">
          <div className="empty-state-icon">★</div>
          <h3 className="empty-state-title">No bookmarks yet</h3>
          <p className="empty-state-body">
            Bookmarks are quick-saves for individual items you want to come back to. Click the
            <strong> ★ star</strong> next to any tweet card or message bubble to bookmark it; it
            will appear here.
          </p>
          <p className="empty-state-hint text-muted">
            For curated multi-item groups, use <strong>📁 Collections</strong>.
            For temporary search-tied scratchpads, use <strong>🔎 Research bins</strong>.
          </p>
        </div>
      )}

      {msgBookmarks && msgBookmarks.length > 0 && (
        <section className="bookmarks-section">
          <h3 className="bookmarks-section-title">Messages</h3>
          {msgBookmarks.map(bm => (
            <div key={bm.msg_key} className="bookmarks-item">
              <MessageBookmarkCard bm={bm} onSelectChat={onSelectChat} />
            </div>
          ))}
        </section>
      )}

      {bookmarks && bookmarks.filter(bm => {
        try { return bm.tweet_meta ? JSON.parse(bm.tweet_meta) : null } catch { return false }
      }).length > 0 && (
        <section className="bookmarks-section">
          <h3 className="bookmarks-section-title">Tweets</h3>
          {bookmarks.map(bm => {
            let tweet = null
            try { tweet = bm.tweet_meta ? JSON.parse(bm.tweet_meta) : null } catch {}
            if (!tweet) return null
            return (
              <div key={bm.article_id} className="bookmarks-item">
                <TweetCard
                  tweet={tweet}
                  articleId={bm.article_id}
                  sourceUrl={bm.url}
                  onSearch={onSearch}
                  onAuthorClick={onAuthorClick}
                  onHashtagClick={onHashtagClick}
                  onMentionClick={onMentionClick}
                />
                {bm.note && <p className="bookmarks-note">{bm.note}</p>}
              </div>
            )
          })}
        </section>
      )}
    </div>
  )
}

export default function App() {
  const [selectedChatId, setSelectedChatId] = useState(null)
  const [selectedChatName, setSelectedChatName] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [view, setView] = useState('thread') // 'thread' | 'ask'
  const [exportOpen, setExportOpen] = useState(false)
  const [authorHandle, setAuthorHandle] = useState(null)
  const [browsePage, setBrowsePage] = useState(null) // {kind: 'hashtag'|'mention', value: string}
  const [showAggregations, setShowAggregations] = useState(false)
  const [showCollections, setShowCollections] = useState(false)
  const [showBookmarks, setShowBookmarks] = useState(false)
  const [showDigests, setShowDigests] = useState(false)
  const [showEntities, setShowEntities] = useState(false)
  const [showScrapeRecovery, setShowScrapeRecovery] = useState(false)
  const [showTopics, setShowTopics] = useState(false)
  const [showExports, setShowExports] = useState(false)
  const [showResearchBins, setShowResearchBins] = useState(false)
  const [researchBinQuery, setResearchBinQuery] = useState('')
  const [researchEntity, setResearchEntity] = useState(null) // entity name string
  const [compareEntities, setCompareEntities] = useState(null) // list of entity names, or null
  // When set, MessageThread jumps to the page containing this ts + scrolls + flashes.
  const [scrollToMessage, setScrollToMessage] = useState(null)

  function handleSelectChat(id, name) {
    setSelectedChatId(id)
    setSelectedChatName(name)
    setSearchResults(null)
    setSearchQuery('')
    setSidebarOpen(false)
    setView('thread')
    setAuthorHandle(null)
    setBrowsePage(null)
    _clearSpecialViews()
  }

  function handleHome() {
    setSelectedChatId(null)
    setSelectedChatName('')
    setSearchResults(null)
    setSearchQuery('')
    setAuthorHandle(null)
    setBrowsePage(null)
    _clearSpecialViews()
    setSidebarOpen(false)
  }

  function handleTrending() {
    _clearSpecialViews()
    setShowAggregations(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSearchQuery('')
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleCollections() {
    _clearSpecialViews()
    setShowCollections(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleBookmarks() {
    _clearSpecialViews()
    setShowBookmarks(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleDigests() {
    _clearSpecialViews()
    setShowDigests(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleEntities() {
    _clearSpecialViews()
    setShowEntities(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleTopics() {
    _clearSpecialViews()
    setShowTopics(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleScrapeRecovery() {
    _clearSpecialViews()
    setShowScrapeRecovery(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleResearchEntity(entityName) {
    _clearSpecialViews()
    setResearchEntity(entityName)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleCompareEntities(entityNames) {
    _clearSpecialViews()
    setCompareEntities(entityNames)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleSearchResults(results, query) {
    setSearchResults(results)
    setSearchQuery(query)
    setSelectedChatId(null)
    setAuthorHandle(null)
    setBrowsePage(null)
    _clearSpecialViews()
  }

  function handleClearSearch() {
    setSearchResults(null)
    setSearchQuery('')
    setAuthorHandle(null)
    setBrowsePage(null)
  }

  function handleOpenInChat(chatId, chatName, ts) {
    // Jump to a specific message within a chat — triggered by "View in chat" links
    // on similar-tweet cards. Sets selection AND scroll target; MessageThread reads
    // both, jumps to the right page, scrolls to the bubble, and flashes it briefly.
    setSelectedChatId(chatId)
    setSelectedChatName(chatName || chatId)
    setSearchResults(null)
    setSearchQuery('')
    setSidebarOpen(false)
    setAuthorHandle(null)
    setBrowsePage(null)
    _clearSpecialViews()
    setScrollToMessage({ chatId, ts })
  }

  function handleResearchBins(query = '') {
    _clearSpecialViews()
    setShowResearchBins(true)
    setResearchBinQuery(query)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function handleExports() {
    _clearSpecialViews()
    setShowExports(true)
    setAuthorHandle(null)
    setBrowsePage(null)
    setSearchResults(null)
    setSelectedChatId(null)
    setSidebarOpen(false)
  }

  function _clearSpecialViews() {
    setShowAggregations(false)
    setShowCollections(false)
    setShowBookmarks(false)
    setShowDigests(false)
    setShowEntities(false)
    setShowScrapeRecovery(false)
    setShowTopics(false)
    setShowExports(false)
    setShowResearchBins(false)
    setResearchEntity(null)
    setCompareEntities(null)
  }

  function handleAuthorClick(handle) {
    setAuthorHandle(handle)
    setBrowsePage(null)
    setSearchResults(null)
    setSearchQuery('')
    _clearSpecialViews()
  }

  function handleHashtagClick(tag) {
    setBrowsePage({ kind: 'hashtag', value: tag })
    setAuthorHandle(null)
    setSearchResults(null)
    setSearchQuery('')
    _clearSpecialViews()
  }

  function handleMentionClick(handle) {
    setBrowsePage({ kind: 'mention', value: handle })
    setAuthorHandle(null)
    setSearchResults(null)
    setSearchQuery('')
    _clearSpecialViews()
  }

  return (
    <GroupProvider>
    <CollectionsProvider>
    <PipelineProgressBar />
    <div className="app-layout">
      <button
        className="sidebar-toggle"
        aria-label="Toggle sidebar"
        onClick={() => setSidebarOpen(o => !o)}
      >
        ☰
      </button>

      <div className={`sidebar-overlay${sidebarOpen ? ' open' : ''}`} onClick={() => setSidebarOpen(false)} />

      <div className={`sidebar-wrap${sidebarOpen ? ' open' : ''}`}>
        <ChatSidebar
          selectedId={selectedChatId}
          onSelectChat={handleSelectChat}
          onSearchResults={handleSearchResults}
          onClearSearch={handleClearSearch}
          onTrending={handleTrending}
          showAggregations={showAggregations}
          onHome={handleHome}
          onCollections={handleCollections}
          onBookmarks={handleBookmarks}
          onDigests={handleDigests}
          onEntities={handleEntities}
          onScrapeRecovery={handleScrapeRecovery}
          onExports={handleExports}
          onResearchBins={handleResearchBins}
          onTopics={handleTopics}
          showCollections={showCollections}
          showBookmarks={showBookmarks}
          showDigests={showDigests}
          showEntities={showEntities}
          showScrapeRecovery={showScrapeRecovery}
          showExports={showExports}
          showResearchBins={showResearchBins}
          showTopics={showTopics}
        />
      </div>

      <main className="main-pane">
        {selectedChatId && (
          <div className="main-toolbar">
            <div className="view-tabs">
              <button className={`tab-btn${view === 'thread' ? ' active' : ''}`} onClick={() => setView('thread')}>Messages</button>
              <button className={`tab-btn${view === 'ask' ? ' active' : ''}`} onClick={() => setView('ask')}>Ask</button>
            </div>
            <button className="btn-export" onClick={() => setExportOpen(true)}>Export…</button>
          </div>
        )}

        {showResearchBins ? (
          <ResearchBinsPage initialQuery={researchBinQuery} />
        ) : showExports ? (
          <ExportsPage />
        ) : compareEntities ? (
          <CompareEntitiesPage
            initialEntities={compareEntities}
            onOpenWorkspace={handleResearchEntity}
          />
        ) : researchEntity ? (
          <ResearchPage
            entityName={researchEntity}
            onEntityClick={(kind, value) => {
              if (kind === 'entity') handleResearchEntity(value)
              else if (kind === 'author') handleAuthorClick(value)
            }}
            onCompare={names => handleCompareEntities(names)}
          />
        ) : showTopics ? (
          <TopicsPage
            onAuthorClick={handleAuthorClick}
            onSearch={handleSearchResults}
          />
        ) : showScrapeRecovery ? (
          <ScrapeRecoveryPage />
        ) : showEntities ? (
          <EntitiesPage onOpenResearch={handleResearchEntity} />
        ) : showDigests ? (
          <DigestsPage />
        ) : showCollections ? (
          <CollectionsPage
            onSearch={handleSearchResults}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
          />
        ) : showBookmarks ? (
          <BookmarksPage
            onSearch={handleSearchResults}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
            onSelectChat={handleSelectChat}
          />
        ) : showAggregations ? (
          <AggregationsPage
            onBack={() => setShowAggregations(false)}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
          />
        ) : browsePage ? (
          <TagBrowsePage
            kind={browsePage.kind}
            value={browsePage.value}
            onBack={() => setBrowsePage(null)}
            onSelectChat={handleSelectChat}
            onSearch={handleSearchResults}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
          />
        ) : authorHandle ? (
          <AuthorPage
            handle={authorHandle}
            onBack={() => setAuthorHandle(null)}
            onSelectChat={handleSelectChat}
            onSearch={handleSearchResults}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
          />
        ) : searchResults !== null ? (
          <SearchResults
            results={searchResults}
            query={searchQuery}
            onSelectChat={handleSelectChat}
            onSearch={handleSearchResults}
            onAuthorClick={handleAuthorClick}
            onHashtagClick={handleHashtagClick}
            onMentionClick={handleMentionClick}
          />
        ) : view === 'ask' && selectedChatId ? (
          <AskPanel chatId={selectedChatId} chatName={selectedChatName} />
        ) : selectedChatId ? (
          <MessageThread chatId={selectedChatId} chatName={selectedChatName} onSearch={handleSearchResults} onAuthorClick={handleAuthorClick} onHashtagClick={handleHashtagClick} onMentionClick={handleMentionClick} scrollToMessage={scrollToMessage} onConsumeScrollTarget={() => setScrollToMessage(null)} onOpenInChat={handleOpenInChat} />
        ) : (
          <EntityHomePage
            onOpenWorkspace={handleResearchEntity}
            onAuthorClick={handleAuthorClick}
            onShowStats={() => {
              setShowAggregations(true)
              setAuthorHandle(null)
              setBrowsePage(null)
              setSearchResults(null)
            }}
          />
        )}
      </main>

      {exportOpen && (
        <ExportModal
          chatId={selectedChatId}
          defaultQuery={searchQuery}
          onClose={() => setExportOpen(false)}
        />
      )}

      <GroupPanel />
    </div>
    </CollectionsProvider>
    </GroupProvider>
  )
}
