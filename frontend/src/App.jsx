import { useEffect, useState } from 'react'

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [tab, setTab] = useState('review')
  const [loginError, setLoginError] = useState('')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw Object.assign(new Error(data.detail || '请求失败'), { status: res.status })
    return data
  }

  async function enter() {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      setLoginError(data.detail || '登录失败')
      return
    }
    localStorage.setItem('print_token', data.access_token)
    localStorage.setItem('print_role', data.role)
    setToken(data.access_token)
    setRole(data.role)
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>投递印张必须声明纸型克重，克重要落在印刷员设定的闭区间里，落在外面直接退回。</p>
        <p>
          <input value={username} onChange={(e) => setUsername(e.target.value)} />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && enter()}
          />
          <button onClick={enter}>登录</button>
        </p>
        {loginError && <p style={{ color: 'crimson' }}>{loginError}</p>}
        <p>printer / print123456 可投递、可设区间；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          borderBottom: '1px solid #999',
          paddingBottom: 8,
          marginBottom: 16,
        }}
      >
        <h1 style={{ margin: 0 }}>印刷套准复核台</h1>
        <nav style={{ display: 'flex', gap: 8 }}>
          <button disabled={tab === 'review'} onClick={() => setTab('review')}>
            复核台
          </button>
          <button disabled={tab === 'weight'} onClick={() => setTab('weight')}>
            纸型克重
          </button>
        </nav>
        <span style={{ marginLeft: 'auto' }}>
          {role === 'writer' ? '印刷员' : '只读账号'}
          <button style={{ marginLeft: 12 }} onClick={leave}>
            退出
          </button>
        </span>
      </header>
      {tab === 'review' ? (
        <ReviewPage api={api} writer={role === 'writer'} />
      ) : (
        <WeightPage api={api} writer={role === 'writer'} onSubmitted={() => setTab('review')} />
      )}
    </main>
  )
}

function ReviewPage({ api, writer }) {
  const [rows, setRows] = useState([])
  const [settings, setSettings] = useState(null)
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [weight, setWeight] = useState('100')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editWeight, setEditWeight] = useState({})

  async function load() {
    const [jobs, s] = await Promise.all([api('/api/jobs'), api('/api/weight/settings')])
    setRows(jobs)
    setSettings(s)
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [])

  async function send() {
    setError('')
    setNotice('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
          weight_gsm: Number(weight),
        }),
      })
      setNotice(`克重 ${weight} g/m² 在区间内，已收下并送检`)
    } catch (err) {
      // 区间外退回
      setError(err.status === 422 ? `退回：${err.message}` : err.message)
    }
  }

  async function saveWeight(id) {
    const value = Number(editWeight[id])
    setError('')
    try {
      await api(`/api/jobs/${id}/weight`, {
        method: 'PATCH',
        body: JSON.stringify({ weight_gsm: value }),
      })
      setEditWeight((m) => ({ ...m, [id]: '' }))
      load()
    } catch (err) {
      setError(err.status === 422 ? `退回：${err.message}` : err.message)
    }
  }

  return (
    <section>
      <p>
        当前克重闭区间：{' '}
        {settings ? (
          <strong>
            {settings.min_gsm} ~ {settings.max_gsm} g/m²（含边界）
          </strong>
        ) : (
          '读取中…'
        )}
      </p>
      {writer && (
        <p>
          <input placeholder="印张" value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input
            placeholder="青偏差 mm"
            value={cyan}
            onChange={(e) => setCyan(e.target.value)}
          />
          <input
            placeholder="品偏差 mm"
            value={magenta}
            onChange={(e) => setMagenta(e.target.value)}
          />
          <input
            placeholder="克重 g/m²"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
            style={{ width: 110 }}
          />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {notice && <p style={{ color: 'seagreen' }}>{notice}</p>}
      {error && <p style={{ color: 'crimson' }}>{error}</p>}
      <table border={1} cellPadding={6} style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th>印张</th>
            <th>青(mm)</th>
            <th>品(mm)</th>
            <th>克重(g/m²)</th>
            <th>状态</th>
            <th>结论</th>
            {writer && <th>改克重</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.sheet}</td>
              <td>{row.cyan_mm}</td>
              <td>{row.magenta_mm}</td>
              <td>{row.weight_gsm}</td>
              <td>{row.status}</td>
              <td>{row.verdict || '等待'}</td>
              {writer && (
                <td>
                  <input
                    placeholder="新克重"
                    style={{ width: 90 }}
                    value={editWeight[row.id] ?? ''}
                    onChange={(e) =>
                      setEditWeight((m) => ({ ...m, [row.id]: e.target.value }))
                    }
                  />
                  <button onClick={() => saveWeight(row.id)}>保存</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function WeightPage({ api, writer, onSubmitted }) {
  const [settings, setSettings] = useState(null)
  const [history, setHistory] = useState([])
  const [minGsm, setMinGsm] = useState('')
  const [maxGsm, setMaxGsm] = useState('')
  const [sheet, setSheet] = useState('插页-03')
  const [cyan, setCyan] = useState('0.05')
  const [magenta, setMagenta] = useState('0.01')
  const [weight, setWeight] = useState('100')
  const [rangeMsg, setRangeMsg] = useState('')
  const [result, setResult] = useState(null)

  async function load() {
    const [s, h] = await Promise.all([api('/api/weight/settings'), api('/api/weight/history')])
    setSettings(s)
    setHistory(h)
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [])

  async function saveRange() {
    setRangeMsg('')
    try {
      const s = await api('/api/weight/settings', {
        method: 'PUT',
        body: JSON.stringify({ min_gsm: Number(minGsm), max_gsm: Number(maxGsm) }),
      })
      setSettings(s)
      setRangeMsg(`区间已更新为 ${s.min_gsm} ~ ${s.max_gsm} g/m²`)
    } catch (err) {
      setRangeMsg(err.message)
    }
  }

  async function submitWeight() {
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
          weight_gsm: Number(weight),
        }),
      })
      setResult({ ok: true, text: `克重 ${weight} g/m² 在闭区间内，收下并送复核` })
      load()
    } catch (err) {
      setResult({
        ok: false,
        text: err.status === 422 ? err.message : `投递失败：${err.message}`,
      })
    }
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <h2>区间设置</h2>
        <p>
          当前闭区间：{' '}
          {settings ? (
            <strong>
              [{settings.min_gsm}, {settings.max_gsm}] g/m²
            </strong>
          ) : (
            '读取中…'
          )}
          {settings && (
            <span style={{ color: '#666' }}>
              {' '}
              （{settings.updated_by} 于 {new Date(settings.updated_at).toLocaleString()} 设定）
            </span>
          )}
        </p>
        {writer ? (
          <p>
            <input
              type="number"
              placeholder="下限"
              value={minGsm}
              onChange={(e) => setMinGsm(e.target.value)}
              style={{ width: 100 }}
            />
            {' ~ '}
            <input
              type="number"
              placeholder="上限"
              value={maxGsm}
              onChange={(e) => setMaxGsm(e.target.value)}
              style={{ width: 100 }}
            />
            {' g/m² '}
            <button onClick={saveRange}>保存区间</button>
            {rangeMsg && <span style={{ marginLeft: 12 }}>{rangeMsg}</span>}
          </p>
        ) : (
          <p style={{ color: '#666' }}>只读账号可查看区间，不能修改区间。</p>
        )}
      </div>

      <div>
        <h2>送检克重栏</h2>
        <p>投递必须声明克重；落在闭区间外（含边界视为合格）一律退回。</p>
        {writer ? (
          <p>
            <input placeholder="印张" value={sheet} onChange={(e) => setSheet(e.target.value)} />
            <input
              placeholder="青偏差 mm"
              value={cyan}
              onChange={(e) => setCyan(e.target.value)}
            />
            <input
              placeholder="品偏差 mm"
              value={magenta}
              onChange={(e) => setMagenta(e.target.value)}
            />
            <input
              placeholder="克重 g/m²"
              value={weight}
              onChange={(e) => setWeight(e.target.value)}
              style={{ width: 110 }}
            />
            <button onClick={submitWeight}>投递</button>
          </p>
        ) : (
          <p style={{ color: '#666' }}>只读账号不能投递。</p>
        )}
        {result && (
          <p style={{ color: result.ok ? 'seagreen' : 'crimson', fontWeight: 'bold' }}>
            {result.ok ? '收下：' : '退回：'}
            {result.text}
          </p>
        )}
        {result?.ok && (
          <button onClick={onSubmitted}>去复核台看结论</button>
        )}
      </div>

      <div>
        <h2>克重履历</h2>
        <p style={{ color: '#666' }}>
          履历只追加：每笔数字是当时的克重原文快照，事后改行的克重不会改动早先那笔。
        </p>
        <table border={1} cellPadding={6} style={{ borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>时间</th>
              <th>印张</th>
              <th>事件</th>
              <th>克重原文(g/m²)</th>
              <th>当时闭区间</th>
              <th>结果</th>
              <th>操作人</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => (
              <tr key={h.id}>
                <td>{new Date(h.created_at).toLocaleString()}</td>
                <td>{h.sheet}</td>
                <td>{h.event}</td>
                <td>{h.weight_gsm}</td>
                <td>
                  [{h.min_gsm}, {h.max_gsm}]
                </td>
                <td>{h.accepted ? '收下' : '退回'}</td>
                <td>{h.created_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
