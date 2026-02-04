import React, { useState, useEffect } from 'react';

export interface ActionPlanData {
    symbol: string;
    stock_name: string;
    action: string;
    time_range: string;
    description: string;
    reasoning: string;
    original_response: string;
}

interface ActionCreationModalProps {
    isOpen: boolean;
    initialData: ActionPlanData | null;
    onClose: () => void;
    onSave: (data: ActionPlanData) => void;
}

export const ActionCreationModal: React.FC<ActionCreationModalProps> = ({ isOpen, initialData, onClose, onSave }) => {
    const [formData, setFormData] = useState<ActionPlanData | null>(null);

    useEffect(() => {
        if (initialData) {
            setFormData(initialData);
        }
    }, [initialData]);

    if (!isOpen || !formData) return null;

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
        const { name, value } = e.target;
        setFormData(prev => prev ? { ...prev, [name]: value } : null);
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (formData) {
            onSave(formData);
        }
    };

    return (
        <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'rgba(0,0,0,0.5)', display: 'flex', justifyContent: 'center', alignItems: 'center',
            zIndex: 1000
        }}>
            <div style={{
                backgroundColor: 'white', padding: '20px', borderRadius: '8px',
                width: '500px', maxHeight: '90vh', overflowY: 'auto', display: 'flex', flexDirection: 'column'
            }}>
                <h3 style={{ marginTop: 0 }}>Create Action Plan</h3>
                <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

                    <div>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold' }}>Symbol</label>
                        <input name="symbol" value={formData.symbol} onChange={handleChange} style={{ width: '100%', padding: '6px' }} />
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold' }}>Action</label>
                        <select name="action" value={formData.action} onChange={handleChange} style={{ width: '100%', padding: '6px' }}>
                            <option value="Buy">Buy</option>
                            <option value="Sell">Sell</option>
                            <option value="Watch">Watch</option>
                        </select>
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold' }}>Time Range</label>
                        <input name="time_range" value={formData.time_range} onChange={handleChange} style={{ width: '100%', padding: '6px' }} />
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold' }}>Description</label>
                        <textarea name="description" value={formData.description} onChange={handleChange} style={{ width: '100%', padding: '6px', minHeight: '60px' }} />
                    </div>

                    <div>
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 'bold' }}>Reasoning</label>
                        <textarea name="reasoning" value={formData.reasoning} onChange={handleChange} style={{ width: '100%', padding: '6px', minHeight: '60px' }} />
                    </div>

                    <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                        <button type="button" onClick={onClose} style={{ padding: '8px 16px', background: '#eee', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>Cancel</button>
                        <button type="submit" style={{ padding: '8px 16px', background: '#007acc', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>Save Action</button>
                    </div>
                </form>
            </div>
        </div>
    );
};
