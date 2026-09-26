import { useEffect, useState } from 'react'

const STATUS_TEXT = { accepted: '收下', rejected: '退回' }
const EVENT_TEXT = { submit: '投递', edit: '改克重' }

const sectionStyle = {
  border: '1px solid #ccc',
  borderRadius: 6,
  padding: '12px 16px',
  marginBottom: 16,
}

export default function GrammagePage({ api, role }) {
  const [range, setRange] = useState(null)
  const [minG, setMinG] = useState('')
  const [maxG, setMaxG] = useState('')
  const [subs, setSubs] = useState([])
  const [history, setHistory] = useState([])
  const [sheet, setSheet] = useState('铜版纸')
  const [grammage, setGrammage] = useState('100')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editValue, setEditValue] = useState('')

  async function load() {
    const [iv, sb, hs] = await Promise.all([
      api('/api/grammage/interval'),
      api('/api/grammage/submissions'),
      api('/api/grammage/history'),
    ])
    setRange(iv)
    setSubs(sb)
    setHistory(hs)
  }

  useEffect(() => {
    load().catch(() => {})
    const timer = setInterval(() => load().catch(() => {}), 1500)
    return () => clearInterval(timer)
  }, [])

  async function saveRange() {
    setError('')
    setMessage('')
    try {
      const iv = await api('/api/grammage/interval', {
        method: 'PUT',
        body: JSON.stringify({ min_grammage: Number(minG), max_grammage: Number(maxG) }),
      })
      setRange(iv)
      setMessage(`克重区间已更新为 ${iv.min_grammage}–${iv.max_grammage}`)
    } catch (err) {
      setError(err.message)
    }
  }

  async function submit() {
    setError('')
    setMessage('')
    try {
      const row = await api('/api/grammage/submissions', {
        method: 'POST',
        body: JSON.stringify({ sheet, grammage: Number(grammage) }),
      })
      setMessage(
        row.status === 'accepted'
          ? `${row.sheet} ${row.grammage} 已收下`
          : `${row.sheet} ${row.grammage} 已退回：不在 ${range.min_grammage}–${range.max_grammage} 区间内`
      )
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function saveEdit(id) {
    setError('')
    setMessage('')
    try {
      const row = await api(`/api/grammage/submissions/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ grammage: Number(editValue) }),
      })
      setEditingId(null)
      setMessage(
        row.status === 'accepted'
          ? `${row.sheet} 克重改为 ${row.grammage}，已收下`
          : `${row.sheet} 克重改为 ${row.grammage}，已退回：不在区间内`
      )
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div>
      <section style={sectionStyle}>
        <h2>区间设置</h2>
        {range && (
          <p>
            当前克重区间：{range.min_grammage} – {range.max_grammage} g/m²（由 {range.updated_by} 更新）
          </p>
        )}
        {role === 'writer' ? (
          <p>
            下限 <input value={minG} onChange={(e) => setMinG(e.target.value)} placeholder="80" size={6} />{' '}
            上限 <input value={maxG} onChange={(e) => setMaxG(e.target.value)} placeholder="120" size={6} />{' '}
            <button onClick={saveRange}>保存区间</button>
          </p>
        ) : (
          <p>只读账号可查看区间，改不了区间。</p>
        )}
      </section>

      <section style={sectionStyle}>
        <h2>送检克重栏</h2>
        {role === 'writer' && (
          <p>
            纸型 <input value={sheet} onChange={(e) => setSheet(e.target.value)} size={10} />{' '}
            克重 <input value={grammage} onChange={(e) => setGrammage(e.target.value)} size={6} /> g/m²{' '}
            <button onClick={submit}>投递</button>
          </p>
        )}
        {message && <p>{message}</p>}
        {error && <p style={{ color: 'crimson' }}>{error}</p>}
        <table>
          <thead>
            <tr><th>纸型</th><th>当前克重</th><th>状态</th><th>送检人</th>{role === 'writer' && <th>操作</th>}</tr>
          </thead>
          <tbody>
            {subs.map((row) => (
              <tr key={row.id}>
                <td>{row.sheet}</td>
                <td>
                  {editingId === row.id ? (
                    <input value={editValue} onChange={(e) => setEditValue(e.target.value)} size={6} />
                  ) : (
                    row.grammage
                  )}
                </td>
                <td>{STATUS_TEXT[row.status] || row.status}</td>
                <td>{row.created_by}</td>
                {role === 'writer' && (
                  <td>
                    {editingId === row.id ? (
                      <>
                        <button onClick={() => saveEdit(row.id)}>保存</button>{' '}
                        <button onClick={() => setEditingId(null)}>取消</button>
                      </>
                    ) : (
                      <button
                        onClick={() => {
                          setEditingId(row.id)
                          setEditValue(String(row.grammage))
                        }}
                      >
                        改克重
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section style={sectionStyle}>
        <h2>克重履历</h2>
        <table>
          <thead>
            <tr><th>时间</th><th>纸型</th><th>克重</th><th>事件</th><th>结果</th><th>操作人</th></tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <tr key={row.id}>
                <td>{new Date(row.recorded_at).toLocaleString()}</td>
                <td>{row.sheet}</td>
                <td>{row.grammage}</td>
                <td>{EVENT_TEXT[row.event] || row.event}</td>
                <td>{STATUS_TEXT[row.status] || row.status}</td>
                <td>{row.recorded_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
